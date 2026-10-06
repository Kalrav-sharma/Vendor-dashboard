// Internal-staff Edge Function behind admin PO Tracking's "Close PO" button (Awaiting supply
// tab, 2026-10-06): closes a purchase order in Uniware itself, via Uniware's Close Purchase
// Order API (https://documentation.unicommerce.com/docs/purchaseorder-close.html), then
// writes Uniware's resulting status onto purchase_orders so the portal reflects it at once
// instead of waiting for the next sync. Closing is permanent in Uniware.
//
// body: { po_code, reason, note? }
//   -> { ok: true, status }                     closed; status = Uniware's new statusCode
//   -> { error }                                anything else (Uniware's own message if it refused)
// Every attempt that reaches Uniware -- refused or not -- is recorded in public.po_close_log.
//
// Caller must be admin or operations (same as the page's canChangePoStage).
//
// Deploy: Supabase dashboard > Edge Functions > deploy new function "close-uniware-po".
// Required secrets: UNIWARE_USERNAME, UNIWARE_PASSWORD -- the same Uniware login the PO sync
// (scripts/sync_to_supabase.py, GitHub secrets) uses. SUPABASE_URL / SUPABASE_ANON_KEY /
// SUPABASE_SERVICE_ROLE_KEY are auto-injected.

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const UNIWARE_USERNAME = Deno.env.get("UNIWARE_USERNAME");
const UNIWARE_PASSWORD = Deno.env.get("UNIWARE_PASSWORD");

const UNIWARE_BASE_URL = "https://urbanclap.unicommerce.com";
const CLOSER_ROLES = new Set(["admin", "operations"]);
const TERMINAL_STATUSES = new Set(["COMPLETE", "REJECTED", "CANCELLED", "CLOSED"]);
const REASONS = new Set(["vendor_cannot_supply", "duplicate_po", "no_longer_needed", "raised_in_error", "other"]);
const UNIWARE_TIMEOUT_MS = 30_000;

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
    if (!authHeader) return json({ error: "Missing Authorization header" }, 401);

    const callerClient = createClient(SUPABASE_URL, ANON_KEY, {
      global: { headers: { Authorization: authHeader } },
    });
    const { data: { user }, error: userErr } = await callerClient.auth.getUser();
    if (userErr || !user) return json({ error: "Not authenticated" }, 401);

    const { data: callerProfile, error: profileErr } = await callerClient
      .from("profiles").select("role, vendor_name, email").eq("id", user.id).single();
    if (profileErr || !callerProfile || !CLOSER_ROLES.has(callerProfile.role)) {
      return json({ error: "Only admin or operations can close a PO" }, 403);
    }

    if (!UNIWARE_USERNAME || !UNIWARE_PASSWORD) {
      return json({ error: "Closing isn't set up yet: UNIWARE_USERNAME / UNIWARE_PASSWORD secrets are missing" }, 500);
    }

    const body = await req.json().catch(() => ({}));
    const poCode = typeof body.po_code === "string" ? body.po_code.trim() : "";
    const reason = typeof body.reason === "string" ? body.reason : "";
    const note = typeof body.note === "string" ? body.note.trim().slice(0, 1000) : "";
    if (!poCode) return json({ error: "po_code is required" }, 400);
    if (!REASONS.has(reason)) return json({ error: "A valid reason is required" }, 400);
    if (reason === "other" && !note) return json({ error: "Add a note when the reason is Other" }, 400);

    const adminClient = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);
    const { data: po, error: poErr } = await adminClient
      .from("purchase_orders").select("po_code, facility, vendor_code, status").eq("po_code", poCode).single();
    if (poErr || !po) return json({ error: `PO ${poCode} isn't on the portal` }, 404);
    if (TERMINAL_STATUSES.has(po.status)) return json({ error: `PO ${poCode} is already ${po.status} in Uniware` }, 409);

    const closerName = callerProfile.vendor_name || callerProfile.email || user.email || null;
    const log = (fields: Record<string, unknown>) => adminClient.from("po_close_log").insert({
      po_code: po.po_code, vendor_code: po.vendor_code, facility: po.facility,
      reason, note: note || null, closed_by: user.id, closed_by_name: closerName,
      previous_status: po.status, ...fields,
    });

    let token: string;
    try {
      token = await uniwareToken();
    } catch (e) {
      return json({ error: `Couldn't log in to Uniware: ${msg(e)}` }, 502);
    }

    let result: any;
    try {
      const resp = await fetch(`${UNIWARE_BASE_URL}/services/rest/v1/purchase/purchaseOrder/close`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `bearer ${token}`, Facility: po.facility },
        body: JSON.stringify({ purchaseOrderCode: po.po_code }),
        signal: AbortSignal.timeout(UNIWARE_TIMEOUT_MS),
      });
      const text = await resp.text();
      try { result = JSON.parse(text); } catch { result = { successful: false, message: `HTTP ${resp.status}: ${text.slice(0, 300)}` }; }
      if (!resp.ok && result.successful !== false) result = { ...result, successful: false, message: result.message || `HTTP ${resp.status}` };
    } catch (e) {
      // Unknown outcome (timeout/network) -- don't record it as refused; the next PO sync shows
      // Uniware's real status either way.
      await log({ successful: null, uniware_message: `No response from Uniware: ${msg(e)}` });
      return json({ error: `No response from Uniware (${msg(e)}). Check the PO in Uniware before trying again.` }, 504);
    }

    if (!result?.successful) {
      const why = (result?.errors || []).map((x: any) => x?.description || x?.message).filter(Boolean).join("; ")
        || result?.message || "Uniware refused the close";
      await log({ successful: false, uniware_message: why });
      return json({ error: `Uniware didn't close it: ${why}` }, 422);
    }

    const newStatus = result.purchaseOrder?.statusCode || "CLOSED";
    await log({ successful: true, new_status: newStatus, uniware_message: result.message || null });
    const { error: updErr } = await adminClient.from("purchase_orders")
      .update({ status: newStatus, updated_at: new Date().toISOString() }).eq("po_code", po.po_code);
    if (updErr) {
      // Closed in Uniware regardless -- the next sync will pick the status up.
      return json({ ok: true, status: newStatus, warning: `Closed in Uniware, but the portal copy didn't update yet: ${updErr.message}` }, 200);
    }
    return json({ ok: true, status: newStatus }, 200);
  } catch (e) {
    return json({ error: `Unexpected error: ${msg(e)}` }, 500);
  }
});

// Same password grant as scripts/sync_to_supabase.py's get_uniware_token().
async function uniwareToken(): Promise<string> {
  const url = new URL(`${UNIWARE_BASE_URL}/oauth/token`);
  url.search = new URLSearchParams({
    grant_type: "password", client_id: "my-trusted-client",
    username: UNIWARE_USERNAME!, password: UNIWARE_PASSWORD!,
  }).toString();
  const resp = await fetch(url, { signal: AbortSignal.timeout(UNIWARE_TIMEOUT_MS) });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || !data.access_token) throw new Error(`HTTP ${resp.status}`);
  return data.access_token;
}

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e));

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
