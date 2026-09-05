// Fetches the REAL, official Uniware PO PDF (not a generated document) and
// streams it back to an authorized caller.
//
// Uniware's own PDF endpoint (/po/show?legacy=1&code=<po_code>) accepts the
// same OAuth bearer token our read-only sync account already uses — no
// separate UI/cookie session needed. That token must never reach the
// browser though, so this Edge Function is the only place it's used: the
// browser calls this function, this function calls Uniware, and only the
// resulting PDF bytes go back to the browser.
//
// Authorization works by piggybacking on the same Row Level Security this
// whole app already relies on: this function queries purchase_orders using
// a client scoped to the CALLER's own JWT (not service_role), so RLS
// itself decides whether the row is visible — a vendor querying a PO that
// isn't theirs gets zero rows back, same as anywhere else in the app. No
// separate admin/vendor branching logic needed here.
//
// Deploy with: supabase functions deploy get-po-pdf
// Required secrets (Project Settings -> Edge Functions -> Secrets, or
// `supabase secrets set`): UNIWARE_USERNAME, UNIWARE_PASSWORD (same
// read-only account already used by the GitHub Actions sync script).
// (SUPABASE_URL and SUPABASE_ANON_KEY are auto-injected by the platform.)

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const UNIWARE_USERNAME = Deno.env.get("UNIWARE_USERNAME")!;
const UNIWARE_PASSWORD = Deno.env.get("UNIWARE_PASSWORD")!;
const UNIWARE_BASE_URL = "https://urbanclap.unicommerce.com";

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

    const url = new URL(req.url);
    const poCode = url.searchParams.get("po_code");
    if (!poCode) {
      return json({ error: "po_code query parameter is required" }, 400);
    }

    // Scoped to the caller's own JWT -- RLS decides visibility. If this
    // vendor doesn't own this PO (or it doesn't exist), we get zero rows.
    const callerClient = createClient(SUPABASE_URL, ANON_KEY, {
      global: { headers: { Authorization: authHeader } },
    });

    const { data: po, error: poErr } = await callerClient
      .from("purchase_orders")
      .select("po_code")
      .eq("po_code", poCode)
      .maybeSingle();

    if (poErr || !po) {
      return json({ error: "Not found or not authorized for this PO" }, 404);
    }

    if (!UNIWARE_USERNAME || !UNIWARE_PASSWORD) {
      return json({ error: "Server misconfigured: missing Uniware credentials" }, 500);
    }

    const tokenResp = await fetch(
      `${UNIWARE_BASE_URL}/oauth/token?` + new URLSearchParams({
        grant_type: "password",
        client_id: "my-trusted-client",
        username: UNIWARE_USERNAME,
        password: UNIWARE_PASSWORD,
      }),
    );
    const tokenData = await tokenResp.json();
    if (!tokenData.access_token) {
      return json({ error: "Failed to authenticate with Uniware" }, 502);
    }

    const pdfResp = await fetch(
      `${UNIWARE_BASE_URL}/po/show?` + new URLSearchParams({ legacy: "1", code: poCode }),
      { headers: { Authorization: `bearer ${tokenData.access_token}` } },
    );

    if (!pdfResp.ok || !(pdfResp.headers.get("Content-Type") || "").includes("pdf")) {
      return json({ error: "Uniware did not return a PDF for this PO" }, 502);
    }

    const pdfBytes = await pdfResp.arrayBuffer();
    const safeFilename = poCode.replace(/[^A-Za-z0-9_-]/g, "_");

    return new Response(pdfBytes, {
      status: 200,
      headers: {
        ...CORS_HEADERS,
        "Content-Type": "application/pdf",
        "Content-Disposition": `attachment; filename="PO_${safeFilename}.pdf"`,
      },
    });
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
