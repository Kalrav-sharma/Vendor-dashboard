// Internal-staff Edge Function (admin/management/operations -- whoever can
// see the Rate Finder page): upserts/deletes rows in vendor_contacts, the
// small hand-maintained "vendor name -> WhatsApp number" table Send Intent
// uses. Not vendor/admin-account lifecycle (that's admin-create-vendor /
// admin-manage-team) -- this is just a phone-number config list, so any
// Rate Finder user can keep it current without needing an admin.
//
// Actions (body.action, defaults to "upsert"):
//   - upsert: { vendor_name, whatsapp_number, contact_name? } -- vendor_name
//     must match a vendor_rates key in mm_rate_card exactly (case-sensitive --
//     that's how Send Intent looks it up). whatsapp_number must be digits
//     only (E.164 without the leading '+', e.g. "919876543210").
//   - delete: { vendor_name }
//
// Deploy with: supabase functions deploy manage-vendor-contacts
// Required secrets: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (same as every
// other Edge Function here; SUPABASE_ANON_KEY is auto-injected).

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const INTERNAL_ROLES = new Set(["admin", "management", "operations"]);

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

    if (profileErr || !INTERNAL_ROLES.has(callerProfile?.role)) {
      return json({ error: "Rate Finder access required" }, 403);
    }

    const body = await req.json();
    const action = body?.action ?? "upsert";
    const adminClient = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);

    if (action === "upsert") {
      const { vendor_name, whatsapp_number, contact_name } = body ?? {};
      if (!vendor_name || !whatsapp_number) {
        return json({ error: "vendor_name and whatsapp_number are required" }, 400);
      }
      if (!/^\d{10,15}$/.test(whatsapp_number)) {
        return json({ error: "whatsapp_number must be digits only, E.164 without a leading '+' (e.g. 919876543210)" }, 400);
      }
      const { error } = await adminClient.from("vendor_contacts").upsert({
        vendor_name, whatsapp_number, contact_name: contact_name ?? null, updated_at: new Date().toISOString(),
      });
      if (error) return json({ error: `Failed to save contact: ${error.message}` }, 400);
      return json({ ok: true });
    }

    if (action === "delete") {
      const { vendor_name } = body ?? {};
      if (!vendor_name) return json({ error: "vendor_name is required" }, 400);
      const { error } = await adminClient.from("vendor_contacts").delete().eq("vendor_name", vendor_name);
      if (error) return json({ error: `Failed to delete contact: ${error.message}` }, 400);
      return json({ ok: true });
    }

    return json({ error: `Unknown action: ${action}` }, 400);
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
