// Admin-ONLY Edge Function: changes an EXISTING login's access type (role)
// to any of the five values -- 'vendor', 'management', 'operations',
// 'finance', or 'admin' -- after it's already been created. This is the
// one place in the whole system that can promote a login to 'admin' via
// the UI (schema.sql's bootstrap comment used to say that stayed manual-
// SQL-only forever; Kalrav explicitly asked for this to be possible from
// the console instead -- the safety property that actually mattered
// (a compromised lower-privilege login can never grant itself admin) is
// still intact, since the CALLER must already be role='admin' to invoke
// this at all).
//
// Deliberately separate from admin-create-vendor / admin-manage-team --
// those two each refuse to act outside their own role (vendor-only /
// management+operations+finance-only), which is exactly why neither one
// can do a cross-role change like this. This function is the one place
// that can move a login between ANY of the five roles.
//
// Also doubles as the general "edit details" save for an EXISTING login
// (name + contact person's name/mobile) when new_role is passed as the
// login's own current, unchanged role -- there's no separate endpoint for
// that, since from this function's point of view it's the same update.
//
// body: { user_id, new_role, vendor_name?, vendor_code?, contact_name?, contact_mobile? }
//   - new_role: 'vendor' | 'management' | 'operations' | 'finance' | 'admin'
//   - Switching TO 'vendor' requires vendor_code, contact_name, contact_mobile
//     (same as admin-create-vendor's own creation requirements) --
//     vendor_name is optional, defaults to vendor_code.
//   - Every OTHER role keeps vendor_code null (profiles.vendor_code must
//     be null for every non-vendor role -- see schema.sql's own documented
//     invariant), but contact_name/contact_mobile are NOT vendor-exclusive
//     -- Kalrav's explicit call: every access type can carry a contact
//     person's name/number, same shape as a vendor, just optional instead
//     of required. vendor_name (the generic display name for every role)
//     is likewise always settable.
//   - Refuses to let a caller change their OWN access level, so an admin
//     can't accidentally lock themselves out via this endpoint.
//
// Deploy with: supabase functions deploy admin-change-access
// Required secrets: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (same as
// every other Edge Function here; SUPABASE_ANON_KEY is auto-injected).

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const VALID_ROLES = new Set(["vendor", "management", "operations", "finance", "admin"]);

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
    const { user_id, new_role, vendor_name, vendor_code, contact_name, contact_mobile } = body ?? {};

    if (!user_id || !new_role) {
      return json({ error: "user_id and new_role are required" }, 400);
    }
    if (!VALID_ROLES.has(new_role)) {
      return json({ error: `new_role must be one of: ${[...VALID_ROLES].join(", ")}` }, 400);
    }
    // Self-edits are fine as long as the role itself isn't changing (e.g.
    // updating your own name/contact info) -- only an actual self ROLE
    // change is blocked, so an admin can't accidentally lock themselves
    // out via this endpoint.
    if (user_id === user.id && new_role !== callerProfile.role) {
      return json({ error: "You can't change your own access level here." }, 400);
    }

    const adminClient = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);

    const { data: target, error: targetErr } = await adminClient
      .from("profiles").select("id").eq("id", user_id).single();
    if (targetErr || !target) {
      return json({ error: "Login not found" }, 404);
    }

    const update: Record<string, unknown> = { role: new_role };
    if (new_role === "vendor") {
      if (!vendor_code || !contact_name || !contact_mobile) {
        return json({ error: "vendor_code, contact_name and contact_mobile are required when switching to Vendor" }, 400);
      }
      update.vendor_code = vendor_code;
      update.vendor_name = vendor_name ?? vendor_code;
      update.contact_name = contact_name;
      update.contact_mobile = contact_mobile;
    } else {
      // vendor_code stays null for every non-vendor role (schema.sql's own
      // documented invariant) -- but contact_name/contact_mobile are NOT
      // vendor-exclusive, so pass through whatever was given (or clear
      // explicitly if the caller sent an empty string) instead of forcing
      // them null.
      update.vendor_code = null;
      if (vendor_name != null) update.vendor_name = vendor_name;
      if (contact_name != null) update.contact_name = contact_name || null;
      if (contact_mobile != null) update.contact_mobile = contact_mobile || null;
    }

    const { error: updateErr } = await adminClient.from("profiles").update(update).eq("id", user_id);
    if (updateErr) {
      return json({ error: `Failed to change access: ${updateErr.message}` }, 400);
    }

    return json({ ok: true });
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
