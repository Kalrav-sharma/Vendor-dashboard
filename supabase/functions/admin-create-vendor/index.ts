// Admin-only Edge Function: creates a new vendor login.
//
// Runs server-side on Supabase's infrastructure — this is the ONLY place
// the service_role key is ever used, and it never leaves this function
// (set as a Supabase secret, not committed anywhere). The browser-side
// admin console calls this via supabase.functions.invoke(), which
// automatically attaches the calling admin's session JWT.
//
// Flow:
//   1. Verify the caller is authenticated AND is an admin (checked with a
//      client scoped to the caller's own JWT, so it's subject to RLS —
//      it can only ever see the caller's own profiles row).
//   2. If not an admin: reject. Nobody else can create logins.
//   3. If admin: use a SEPARATE client built with the service_role key to
//      create the auth user and insert their profiles row, atomically
//      enough for this use case (if the profile insert fails, the auth
//      user is deleted so we don't leave an orphaned login with no
//      vendor_code assigned).
//
// Deploy with: supabase functions deploy admin-create-vendor
// Required secrets (Project Settings -> Edge Functions -> Secrets, or
// `supabase secrets set`): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
// (SUPABASE_URL and SUPABASE_ANON_KEY are auto-injected by the platform;
// only SUPABASE_SERVICE_ROLE_KEY needs to be set explicitly).

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

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

    if (profileErr || callerProfile?.role !== "admin") {
      return json({ error: "Admin access required" }, 403);
    }

    const body = await req.json();
    const { email, password, vendor_code, vendor_name } = body ?? {};
    if (!email || !password || !vendor_code) {
      return json({ error: "email, password and vendor_code are required" }, 400);
    }
    if (String(password).length < 8) {
      return json({ error: "Password must be at least 8 characters" }, 400);
    }

    // Elevated client -- service_role bypasses RLS entirely. Only used
    // from here on, only for the two writes this endpoint exists to do.
    const adminClient = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);

    const { data: created, error: createErr } = await adminClient.auth.admin.createUser({
      email,
      password,
      email_confirm: true,
    });
    if (createErr || !created?.user) {
      return json({ error: `Failed to create login: ${createErr?.message ?? "unknown error"}` }, 400);
    }

    const { error: insertErr } = await adminClient.from("profiles").insert({
      id: created.user.id,
      role: "vendor",
      vendor_code,
      vendor_name: vendor_name ?? null,
      email,
    });

    if (insertErr) {
      // Don't leave an orphaned auth user with no profile/vendor_code.
      await adminClient.auth.admin.deleteUser(created.user.id);
      return json({ error: `Failed to assign vendor profile: ${insertErr.message}` }, 400);
    }

    return json({ ok: true, user_id: created.user.id, email, vendor_code });
  } catch (e) {
    return json({ error: `Unexpected error: ${e instanceof Error ? e.message : String(e)}` }, 500);
  }
});

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
