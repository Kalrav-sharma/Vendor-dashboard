// Internal-staff Edge Function (admin/management/operations): sends the
// "Send Intent" WhatsApp message via Meta's official WhatsApp Business
// Cloud API (Graph API), using a pre-approved message TEMPLATE -- required
// because this is a business-initiated message with no open 24-hour
// customer-service window (a vendor hasn't necessarily messaged us first).
//
// Looks up the target vendor's number from vendor_contacts (maintained via
// manage-vendor-contacts) -- if none is saved, or if the WhatsApp secrets
// below aren't configured yet, returns a clear error rather than silently
// no-oping, so the rest of Rate Finder (lookup + vendor selection) stays
// fully testable before WhatsApp sending itself is wired up.
//
// Expected template (submit this exact shape to Meta for approval, then
// set WHATSAPP_TEMPLATE_NAME to whatever name you give it):
//   Category: UTILITY
//   Body: "New shipment intent from Native/UC.\nPickup: {{1}}\nDrop: {{2}}\n
//          Truck size: {{3}}\nRate: Rs.{{4}}\nPlease confirm availability."
//   -> 4 body placeholders, in this exact order: pickup city, drop city,
//      truck size, rate.
//
// body: { vendor_name, pickup_city, drop_city, truck_size, rate }
//
// Deploy with: supabase functions deploy send-vendor-intent
// Required secrets:
//   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  -- same as every function here
//   WHATSAPP_ACCESS_TOKEN     -- Meta WhatsApp Cloud API permanent token
//   WHATSAPP_PHONE_NUMBER_ID  -- the Cloud API sending number's ID
//   WHATSAPP_TEMPLATE_NAME    -- the approved template's name
//   WHATSAPP_TEMPLATE_LANG    -- optional, defaults to "en"

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const WHATSAPP_ACCESS_TOKEN = Deno.env.get("WHATSAPP_ACCESS_TOKEN");
const WHATSAPP_PHONE_NUMBER_ID = Deno.env.get("WHATSAPP_PHONE_NUMBER_ID");
const WHATSAPP_TEMPLATE_NAME = Deno.env.get("WHATSAPP_TEMPLATE_NAME");
const WHATSAPP_TEMPLATE_LANG = Deno.env.get("WHATSAPP_TEMPLATE_LANG") || "en";

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

    if (!WHATSAPP_ACCESS_TOKEN || !WHATSAPP_PHONE_NUMBER_ID || !WHATSAPP_TEMPLATE_NAME) {
      return json({ error: "WhatsApp sending isn't configured yet -- ask Kalrav to set the WHATSAPP_* secrets on this function." }, 501);
    }

    const { vendor_name, pickup_city, drop_city, truck_size, rate } = await req.json();
    if (!vendor_name || !pickup_city || !drop_city || !truck_size || rate == null) {
      return json({ error: "vendor_name, pickup_city, drop_city, truck_size and rate are all required" }, 400);
    }

    const adminClient = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);
    const { data: contact, error: contactErr } = await adminClient
      .from("vendor_contacts").select("whatsapp_number").eq("vendor_name", vendor_name).single();
    if (contactErr || !contact) {
      return json({ error: `No WhatsApp contact saved for "${vendor_name}" -- add one in Rate Finder first.` }, 404);
    }

    const waResp = await fetch(`https://graph.facebook.com/v20.0/${WHATSAPP_PHONE_NUMBER_ID}/messages`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${WHATSAPP_ACCESS_TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        messaging_product: "whatsapp",
        to: contact.whatsapp_number,
        type: "template",
        template: {
          name: WHATSAPP_TEMPLATE_NAME,
          language: { code: WHATSAPP_TEMPLATE_LANG },
          components: [{
            type: "body",
            parameters: [
              { type: "text", text: String(pickup_city) },
              { type: "text", text: String(drop_city) },
              { type: "text", text: String(truck_size) },
              { type: "text", text: String(rate) },
            ],
          }],
        },
      }),
    });

    const waData = await waResp.json();
    if (!waResp.ok) {
      return json({ error: `WhatsApp send failed: ${waData?.error?.message || waResp.statusText}` }, 400);
    }

    return json({ ok: true, message_id: waData?.messages?.[0]?.id ?? null });
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
