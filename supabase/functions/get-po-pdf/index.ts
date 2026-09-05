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

// Cached across invocations for as long as this function instance stays
// warm (module-level state persists between calls on the same instance,
// same as any long-lived process — it just isn't guaranteed to survive a
// cold start). Measured live: Uniware's token is valid for ~8 hours, so
// re-fetching it on every single download was pure wasted latency (~0.2-1.3s
// of the reported 5-10s) — this skips that round-trip whenever the cache
// is still fresh, refreshing it a couple of minutes before it would expire.
let cachedUniwareToken: { token: string; expiresAt: number } | null = null;
// Several downloads clicked close together can land on the same warm
// instance concurrently. Without this, each one would see the cache as
// empty/expired at the same time and fire its own simultaneous password-
// grant request -- suspected cause of the intermittent 502s seen when
// clicking the button a few times in quick succession. This makes every
// concurrent caller await the SAME in-flight fetch instead of racing.
let inFlightTokenFetch: Promise<string> | null = null;

async function getUniwareToken(): Promise<string> {
  if (cachedUniwareToken && Date.now() < cachedUniwareToken.expiresAt) {
    return cachedUniwareToken.token;
  }
  if (inFlightTokenFetch) {
    return inFlightTokenFetch;
  }
  inFlightTokenFetch = (async () => {
    try {
      const tokenResp = await fetch(
        `${UNIWARE_BASE_URL}/oauth/token?` + new URLSearchParams({
          grant_type: "password",
          client_id: "my-trusted-client",
          username: UNIWARE_USERNAME,
          password: UNIWARE_PASSWORD,
        }),
      );
      const rawBody = await tokenResp.text();
      if (!tokenResp.ok) {
        console.error(`Uniware oauth/token HTTP ${tokenResp.status}: ${rawBody.slice(0, 500)}`);
        throw new Error("Failed to authenticate with Uniware");
      }
      let tokenData: { access_token?: string; expires_in?: number };
      try {
        tokenData = JSON.parse(rawBody);
      } catch {
        console.error(`Uniware oauth/token returned non-JSON: ${rawBody.slice(0, 500)}`);
        throw new Error("Failed to authenticate with Uniware");
      }
      if (!tokenData.access_token) {
        console.error(`Uniware oauth/token had no access_token: ${rawBody.slice(0, 500)}`);
        throw new Error("Failed to authenticate with Uniware");
      }
      const expiresInMs = (Number(tokenData.expires_in) || 300) * 1000;
      cachedUniwareToken = {
        token: tokenData.access_token,
        expiresAt: Date.now() + expiresInMs - 120_000, // refresh 2 min early
      };
      return cachedUniwareToken.token;
    } finally {
      inFlightTokenFetch = null;
    }
  })();
  return inFlightTokenFetch;
}

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

    let uniwareToken: string;
    try {
      uniwareToken = await getUniwareToken();
    } catch {
      return json({ error: "Failed to authenticate with Uniware" }, 502);
    }

    const pdfResp = await fetch(
      `${UNIWARE_BASE_URL}/po/show?` + new URLSearchParams({ legacy: "1", code: poCode }),
      { headers: { Authorization: `bearer ${uniwareToken}` } },
    );

    const pdfContentType = pdfResp.headers.get("Content-Type") || "";
    if (!pdfResp.ok || !pdfContentType.includes("pdf")) {
      const bodySnippet = await pdfResp.text().catch(() => "");
      console.error(
        `Uniware po/show HTTP ${pdfResp.status} content-type=${pdfContentType} for ${poCode}: ${bodySnippet.slice(0, 500)}`,
      );
      return json({ error: `Uniware did not return a PDF for this PO (HTTP ${pdfResp.status})` }, 502);
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
