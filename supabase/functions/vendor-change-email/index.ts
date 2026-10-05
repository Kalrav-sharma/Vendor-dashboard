// Self-service Edge Function: lets a signed-in vendor set their own real
// email address, replacing whatever placeholder/dummy email their login
// was created with (see admin-create-vendor). Driven from VendorApp.vue's
// one-time "confirm your email" gate (profiles.must_change_email), but not
// itself restricted to firing only once -- a vendor who mistypes their
// email can call this again later the same way.
//
// Unlike vendor-code-auth, this function DOES require the caller's own JWT
// (default "Enforce JWT verification" ON) -- it only ever acts on the
// calling vendor's own account, never anyone else's.
//
// Design, mirrors admin-create-vendor's own privilege split:
//   1. A client scoped to the caller's JWT resolves who's calling and
//      checks their profiles.role -- rejects anyone who isn't a vendor.
//   2. Only then does a SEPARATE service_role client update auth.users
//      (via the Admin API, not raw SQL, so Supabase's own auth internals
//      stay consistent) and the profiles row.
//
// email_confirm: true mirrors admin-create-vendor's own vendor creation --
// this project treats a vendor's email as already trusted/confirmed rather
// than gating it behind a "click the link we just sent your new address"
// round trip, since the vendor is already authenticated when they set it.
//
// body: { new_email }
//   -> { ok: true } on success.
//   -> { error: "..." } on failure (bad/duplicate email, not a vendor, etc).
//
// Deployed on Supabase under the name "Vendor-Change-Email" (capitalized --
// unlike every other function here, which is all-lowercase; function names
// are case-sensitive in the invoke URL, so the frontend's
// supabase.functions.invoke() call in SetVendorEmailForm.vue matches that
// exact capitalization, not this folder's name).
// Required secrets: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (SUPABASE_URL
// and SUPABASE_ANON_KEY are auto-injected by the platform).

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const CHANGES_PER_USER = { limit: 10, windowSeconds: 60 * 60 };

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS_HEADERS });
  }

  try {
    const authHeader = req.headers.get("Authorization");
    if (!authHeader) {
      return json({ error: "Missing Authorization header" }, 401);
    }

    // Client scoped to the caller's own JWT -- respects RLS, so this can
    // only ever read the caller's OWN profiles row, nothing else.
    const callerClient = createClient(SUPABASE_URL, ANON_KEY, {
      global: { headers: { Authorization: authHeader } },
    });

    const { data: { user }, error: userErr } = await callerClient.auth.getUser();
    if (userErr || !user) {
      return json({ error: "Not authenticated" }, 401);
    }

    const { data: callerProfile, error: profileErr } = await callerClient
      .from("profiles")
      .select("role")
      .eq("id", user.id)
      .single();

    if (profileErr || callerProfile?.role !== "vendor") {
      return json({ error: "Vendor access required" }, 403);
    }

    const body = await req.json();
    const newEmail = String(body?.new_email ?? "").trim().toLowerCase();
    if (!newEmail || newEmail.length > 254 || !EMAIL_RE.test(newEmail)) {
      return json({ error: "Enter a valid email address." }, 400);
    }

    // Elevated client -- service_role bypasses RLS entirely. Only used
    // from here on, only for this one caller's own auth user + profile row.
    const adminClient = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);

    if (!(await underLimit(adminClient, `vendor-change-email:${user.id}`, CHANGES_PER_USER))) {
      return json({ error: "Too many email changes in a short time -- please wait a while and try again." }, 429);
    }

    const { error: updateAuthErr } = await adminClient.auth.admin.updateUserById(user.id, {
      email: newEmail,
      email_confirm: true,
    });
    if (updateAuthErr) {
      const msg = /already been registered|already exists/i.test(updateAuthErr.message)
        ? "That email is already in use by another login."
        : updateAuthErr.message;
      return json({ error: msg }, 400);
    }

    const { error: updateProfileErr } = await adminClient
      .from("profiles")
      .update({ email: newEmail, must_change_email: false })
      .eq("id", user.id);
    if (updateProfileErr) {
      return json({ error: `Email updated, but failed to save it to your profile: ${updateProfileErr.message}` }, 200);
    }

    return json({ ok: true }, 200);
  } catch (e) {
    return json({ error: `Unexpected error: ${e instanceof Error ? e.message : String(e)}` }, 500);
  }
});

// Fixed-window limiter via rate_limit_hit() (schema.sql); fails open so a missing/broken limiter never blocks the change.
async function underLimit(adminClient: any, bucket: string, cfg: { limit: number; windowSeconds: number }) {
  const { data, error } = await adminClient.rpc("rate_limit_hit", {
    p_bucket: bucket, p_limit: cfg.limit, p_window_seconds: cfg.windowSeconds, p_cost: 1,
  });
  if (error) {
    console.error(`rate_limit_hit(${bucket}) failed, allowing request: ${error.message}`);
    return true;
  }
  return data !== false;
}

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
