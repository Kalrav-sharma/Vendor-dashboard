// Admin-ONLY Edge Function: creates, revokes/restores, and deletes internal
// UC-staff logins (profiles.role in 'management', 'operations', 'finance').
// Sibling of admin-create-vendor, kept as a SEPARATE function rather than
// folded into that one -- vendor logins and internal-staff logins are
// related but distinct concerns (different required fields, and this
// function must never be usable to touch a vendor or an admin row), and
// keeping them apart avoids either function's action-handling growing
// role-conditional branches for the other's shape.
//
// Unlike admin-create-vendor (whose revoke/restore/delete are usable by
// anyone with role='admin'), EVERY action here -- including revoke/restore/
// delete of an EXISTING internal-staff login -- requires the caller to be
// role==='admin' exactly. This is deliberate: the user's requirement is
// that "management" gets full portal access barring the ability to create
// (or otherwise administer) new user access, and internal-staff account
// lifecycle is exactly that "admin level control".
//
// Runs server-side on Supabase's infrastructure -- this is the ONLY place
// the service_role key is ever used, and it never leaves this function
// (set as a Supabase secret, not committed anywhere). The browser-side
// admin console calls this via supabase.functions.invoke(), which
// automatically attaches the calling admin's session JWT.
//
// Actions (body.action, defaults to "create" for backward compatibility):
//   - create:  { email, display_name, role } -- role must be one of
//     'management' | 'operations' | 'finance' (never 'admin' or 'vendor').
//     Creates the auth user (password always DEFAULT_TEMP_PASSWORD below,
//     same constant as admin-create-vendor's -- keep the two in sync if
//     this ever changes) + profiles row with must_change_password=true,
//     and returns the temp password so the admin console can display it.
//   - revoke:  { user_id } -- bans the auth user via Supabase Auth's own
//     ban_duration (real enforcement, not just a hidden UI button), and
//     mirrors it onto profiles.revoked.
//   - restore: { user_id } -- clears the ban and profiles.revoked.
//   - delete:  { user_id } -- permanently deletes the auth user; cascades
//     to delete their profiles row (see the FK in schema.sql).
//
// revoke/restore/delete all refuse to act on anything other than a
// management/operations/finance profile, so this endpoint can never be
// used to touch a vendor login or another admin account.
//
// Deploy with: supabase functions deploy admin-manage-team
// Required secrets: same as admin-create-vendor (SUPABASE_URL,
// SUPABASE_SERVICE_ROLE_KEY -- SUPABASE_ANON_KEY is auto-injected).

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const INDEFINITE_BAN = "876000h"; // 100 years -- see admin-create-vendor for why

const TEAM_ROLES = new Set(["management", "operations", "finance"]);

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

    // Deliberately narrower than admin-create-vendor's check: exactly
    // 'admin', never 'management' -- see the file header.
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
      return await handleTeamAction(adminClient, action, body);
    }
    return json({ error: `Unknown action: ${action}` }, 400);
  } catch (e) {
    return json({ error: `Unexpected error: ${e instanceof Error ? e.message : String(e)}` }, 500);
  }
});

// Must match admin-create-vendor's DEFAULT_TEMP_PASSWORD -- see that
// function's comment for why a single shared constant is safe here.
const DEFAULT_TEMP_PASSWORD = "Native@01";

async function handleCreate(adminClient: ReturnType<typeof createClient>, body: any) {
  const { email, display_name, role } = body ?? {};
  if (!email || !role) {
    return json({ error: "email and role are required" }, 400);
  }
  if (!TEAM_ROLES.has(role)) {
    return json({ error: `role must be one of: ${[...TEAM_ROLES].join(", ")}` }, 400);
  }

  const { data: created, error: createErr } = await adminClient.auth.admin.createUser({
    email,
    password: DEFAULT_TEMP_PASSWORD,
    email_confirm: true,
  });
  if (createErr || !created?.user) {
    return json({ error: `Failed to create login: ${createErr?.message ?? "unknown error"}` }, 400);
  }

  const { error: insertErr } = await adminClient.from("profiles").insert({
    id: created.user.id,
    role,
    vendor_code: null,
    vendor_name: display_name ?? null,
    email,
    must_change_password: true,
  });

  if (insertErr) {
    // Don't leave an orphaned auth user with no profile.
    await adminClient.auth.admin.deleteUser(created.user.id);
    return json({ error: `Failed to assign profile: ${insertErr.message}` }, 400);
  }

  return json({ ok: true, user_id: created.user.id, email, role, temp_password: DEFAULT_TEMP_PASSWORD });
}

async function handleTeamAction(adminClient: ReturnType<typeof createClient>, action: string, body: any) {
  const { user_id } = body ?? {};
  if (!user_id) {
    return json({ error: "user_id is required" }, 400);
  }

  // Refuse to touch anything that isn't an internal-staff login -- this
  // endpoint must never be usable on a vendor or another admin account.
  const { data: targetProfile, error: targetErr } = await adminClient
    .from("profiles").select("role").eq("id", user_id).single();
  if (targetErr || !targetProfile) {
    return json({ error: "Team login not found" }, 404);
  }
  if (!TEAM_ROLES.has(targetProfile.role)) {
    return json({ error: "This action can only be used on management/operations/finance logins" }, 400);
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
