// Admin-only Edge Function: creates, revokes/restores, and deletes vendor
// logins. (Name kept as admin-create-vendor even though it now does more
// than create, so updating it is a redeploy of an existing function
// rather than needing a brand new one set up in Supabase.)
//
// Runs server-side on Supabase's infrastructure — this is the ONLY place
// the service_role key is ever used, and it never leaves this function
// (set as a Supabase secret, not committed anywhere). The browser-side
// admin console calls this via supabase.functions.invoke(), which
// automatically attaches the calling admin's session JWT.
//
// Every action:
//   1. Verifies the caller is authenticated AND is an admin (checked with
//      a client scoped to the caller's own JWT, so it's subject to RLS —
//      it can only ever see the caller's own profiles row).
//   2. If not an admin: reject.
//   3. Only then uses a SEPARATE client built with the service_role key
//      for the actual privileged operation.
//
// Actions (body.action, defaults to "create" for backward compatibility
// with callers that don't send it):
//   - create:  { email, password, vendor_code, vendor_name } -- unchanged
//     from before: creates the auth user + profiles row.
//   - revoke:  { user_id } -- bans the auth user via Supabase Auth's own
//     ban_duration (real enforcement: signInWithPassword fails outright,
//     this isn't just a hidden UI button), and mirrors it onto
//     profiles.revoked so the admin console can display the right state
//     without needing service_role access itself.
//   - restore: { user_id } -- clears the ban and profiles.revoked.
//   - delete:  { user_id } -- permanently deletes the auth user; cascades
//     to delete their profiles row (see the FK in schema.sql). Historical
//     PO/GRN data is untouched -- it's keyed by vendor_code, not by this
//     row's id.
//
// revoke/restore/delete all refuse to act on a non-vendor (e.g. an admin)
// profile, so this endpoint can't be used to lock out an admin account.
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

// Supabase's ban_duration takes a duration string, not a boolean -- there's
// no native "banned forever", so this is the same "very long duration"
// convention Supabase's own docs use to mean "indefinite, until unbanned".
const INDEFINITE_BAN = "876000h"; // 100 years

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
    const action = body?.action ?? "create";

    // Elevated client -- service_role bypasses RLS entirely. Only used
    // from here on, only for the privileged operation each action needs.
    const adminClient = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);

    if (action === "create") {
      return await handleCreate(adminClient, body);
    }
    if (action === "revoke" || action === "restore" || action === "delete") {
      return await handleVendorAction(adminClient, action, body);
    }
    return json({ error: `Unknown action: ${action}` }, 400);
  } catch (e) {
    return json({ error: `Unexpected error: ${e instanceof Error ? e.message : String(e)}` }, 500);
  }
});

async function handleCreate(adminClient: ReturnType<typeof createClient>, body: any) {
  const { email, password, vendor_code, vendor_name } = body ?? {};
  if (!email || !password || !vendor_code) {
    return json({ error: "email, password and vendor_code are required" }, 400);
  }
  if (String(password).length < 8) {
    return json({ error: "Password must be at least 8 characters" }, 400);
  }

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
}

async function handleVendorAction(adminClient: ReturnType<typeof createClient>, action: string, body: any) {
  const { user_id } = body ?? {};
  if (!user_id) {
    return json({ error: "user_id is required" }, 400);
  }

  // Refuse to touch anything that isn't a vendor login -- this endpoint
  // must never be usable to revoke/delete an admin account.
  const { data: targetProfile, error: targetErr } = await adminClient
    .from("profiles").select("role").eq("id", user_id).single();
  if (targetErr || !targetProfile) {
    return json({ error: "Vendor login not found" }, 404);
  }
  if (targetProfile.role !== "vendor") {
    return json({ error: "This action can only be used on vendor logins" }, 400);
  }

  if (action === "delete") {
    const { error } = await adminClient.auth.admin.deleteUser(user_id);
    if (error) return json({ error: `Failed to delete login: ${error.message}` }, 400);
    return json({ ok: true });
  }

  const banDuration = action === "revoke" ? INDEFINITE_BAN : "none";
  const { error: banErr } = await adminClient.auth.admin.updateUserById(user_id, { ban_duration: banDuration });
  if (banErr) {
    return json({ error: `Failed to ${action} access: ${banErr.message}` }, 400);
  }

  const { error: updateErr } = await adminClient
    .from("profiles").update({ revoked: action === "revoke" }).eq("id", user_id);
  if (updateErr) {
    return json({ error: `Access ${action === "revoke" ? "revoked" : "restored"}, but failed to update display status: ${updateErr.message}` }, 200);
  }

  return json({ ok: true });
}

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
