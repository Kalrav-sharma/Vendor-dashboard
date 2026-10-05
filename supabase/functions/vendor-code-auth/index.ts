// Public Edge Function (NO caller auth) -- lets a vendor sign in with their
// shared vendor_code + password instead of an individual email, and lets
// them trigger a password reset the same way. This is the ONLY Edge
// Function in this project that must be deployed with the Supabase
// Dashboard's "Enforce JWT verification" toggle turned OFF, since it has
// to be callable before anyone is signed in at all.
//
// Design: there is still exactly ONE real Supabase Auth account per vendor
// (one email = the vendor's recovery email, one password) -- vendor_code
// is just an ALTERNATE identifier that resolves to that one real account.
// The resolved email never leaves this function; every downstream call
// (signInWithPassword / resetPasswordForEmail) also happens server-side,
// so a vendor_code lookup can never be used to harvest a vendor's real
// email address, and every failure mode below collapses to the SAME
// generic response -- unknown code, wrong password, revoked account, and
// a rate-limit lockout are all indistinguishable from outside, same
// anti-enumeration principle LoginPage.vue's own email-based reset already
// follows ("Supabase deliberately doesn't reveal whether the email exists").
//
// body: { action: "signin", vendor_code, password }
//   -> { access_token, refresh_token } on success (frontend calls
//      supabase.auth.setSession(...) with these on the SAME shared client
//      every other page uses, so the rest of the app can't tell this apart
//      from a normal signInWithPassword login).
//   -> { error: GENERIC_ERROR } on any failure.
// body: { action: "reset", identifier, redirect_to }  -- identifier is
//   always a vendor_code here; LoginPage.vue only ever routes non-email-
//   looking input to this function, calling resetPasswordForEmail directly
//   itself for anything with an "@". redirect_to is computed by the caller
//   exactly like LoginPage.vue's existing email-based reset already does
//   (new URL("reset-password.html", window.location.href).href).
//   -> always { ok: true, message: GENERIC_RESET_MESSAGE }, regardless of
//      whether the vendor_code actually resolved to anything.
//
// Rate limiting: public.vendor_login_attempts (service_role-only, no RLS
// needed -- never queried by any client). 10 failed attempts per
// vendor_code within a rolling window locks that CODE out for 15 minutes.
// This necessarily locks out every person sharing that vendor_code at
// once, not just one person -- an accepted trade-off of the shared-
// credential model, not a bug; the threshold is deliberately generous
// for that reason. Failures are counted atomically (vendor_login_record_failure()),
// plus per-IP / per-code caps via rate_limit_hit(); every limiter fails open.
// redirect_to must be this app's reset-password.html (ALLOWED_REDIRECT_ORIGINS).
//
// Deploy with: supabase functions deploy vendor-code-auth --no-verify-jwt
// (or toggle "Enforce JWT verification" OFF for this function in the
// Dashboard if deploying by pasting, per this project's usual manual
// deploy process -- see CLAUDE.md / IMPLEMENTATION notes).
// Required secrets: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (same as every
// other function here; SUPABASE_ANON_KEY is auto-injected by the platform).

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const GENERIC_SIGNIN_ERROR = "Incorrect vendor code or password.";
const GENERIC_RESET_MESSAGE = "If an account exists for that vendor code, a password reset link has been sent to its recovery email.";
// Auth's per-IP limit sees every vendor-code sign-in as this function's IP; hitting it isn't the vendor's failure.
const BUSY_MESSAGE = "Sign-in is busy right now -- please try again in a minute.";
const GENERIC_SERVER_ERROR = "Something went wrong. Please try again.";

// A fixed, guaranteed-nonexistent credential -- used to burn roughly the
// same amount of time as a real signInWithPassword call when the
// vendor_code lookup itself misses, so response latency doesn't leak
// whether a code exists even though the error text already doesn't.
const DUMMY_EMAIL = "no-such-account@vendor.invalid";
const DUMMY_PASSWORD = "not-a-real-password";

const MAX_ATTEMPTS = 10;
const LOCKOUT_MINUTES = 15;

const IP_SIGNIN_FAILURES = { limit: 30, windowSeconds: 15 * 60 };
const IP_RESETS = { limit: 10, windowSeconds: 60 * 60 };
const CODE_RESETS = { limit: 3, windowSeconds: 60 * 60 };

const MAX_FIELD_LENGTH = 256;

const ALLOWED_REDIRECT_ORIGINS = new Set(
  (Deno.env.get("ALLOWED_REDIRECT_ORIGINS") ?? "https://kalrav-sharma.github.io")
    .split(",").map((s) => s.trim().replace(/\/+$/, "")).filter(Boolean),
);

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS_HEADERS });
  }

  try {
    const body = await req.json();
    const action = body?.action;

    const adminClient = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);
    const ip = clientIp(req);

    if (action === "signin") {
      return await handleSignin(adminClient, body, ip);
    }
    if (action === "reset") {
      return await handleReset(adminClient, body, ip);
    }
    return json({ error: "Unknown action" }, 400);
  } catch (e) {
    console.error(`vendor-code-auth unexpected error: ${e instanceof Error ? e.message : String(e)}`);
    return json({ error: GENERIC_SERVER_ERROR }, 500);
  }
});

// Untyped schema: ReturnType<typeof createClient> would type every rpc() arg as undefined.
type Db = any;
type Limit = { limit: number; windowSeconds: number };

function isShortString(v: unknown): v is string {
  return typeof v === "string" && v.length > 0 && v.length <= MAX_FIELD_LENGTH;
}

// null (skip per-IP limits) when unknown or private, so clients are never lumped into one shared bucket.
function clientIp(req: Request): string | null {
  const raw = req.headers.get("cf-connecting-ip") ?? req.headers.get("x-forwarded-for")?.split(",")[0] ?? "";
  const ip = raw.trim().toLowerCase();
  if (!ip || ip.length > 64) return null;
  if (/^(10\.|127\.|0\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|::1$|::$|f[cd][0-9a-f]{2}:|fe80:)/.test(ip)) return null;
  return ip;
}

// cost 0 = check without counting. Fails open so a missing/broken limiter never blocks sign-in.
async function underLimit(db: Db, bucket: string, cfg: Limit, cost = 1): Promise<boolean> {
  const { data, error } = await db.rpc("rate_limit_hit", {
    p_bucket: bucket, p_limit: cfg.limit, p_window_seconds: cfg.windowSeconds, p_cost: cost,
  });
  if (error) {
    console.error(`rate_limit_hit(${bucket}) failed, allowing request: ${error.message}`);
    return true;
  }
  return data !== false;
}

function isAllowedRedirect(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 2048) return false;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  const isLocalDev = url.protocol === "http:" && (url.hostname === "localhost" || url.hostname === "127.0.0.1");
  if (!isLocalDev && !(url.protocol === "https:" && ALLOWED_REDIRECT_ORIGINS.has(url.origin))) return false;
  return url.pathname.endsWith("/reset-password.html");
}

async function isLockedOut(adminClient: Db, vendorCode: string): Promise<boolean> {
  const { data } = await adminClient
    .from("vendor_login_attempts").select("locked_until").eq("vendor_code", vendorCode).maybeSingle();
  return !!(data?.locked_until && new Date(data.locked_until).getTime() > Date.now());
}

async function recordFailure(adminClient: Db, vendorCode: string) {
  const { error } = await adminClient.rpc("vendor_login_record_failure", {
    p_vendor_code: vendorCode, p_max_attempts: MAX_ATTEMPTS, p_lockout_minutes: LOCKOUT_MINUTES,
  });
  if (!error) return;

  // Fallback until the SQL migration is applied: the original (racy) read-then-write.
  console.error(`vendor_login_record_failure failed, using fallback: ${error.message}`);
  const { data } = await adminClient
    .from("vendor_login_attempts").select("failed_count").eq("vendor_code", vendorCode).maybeSingle();
  const nextCount = (data?.failed_count ?? 0) + 1;
  const lockedOut = nextCount >= MAX_ATTEMPTS;
  await adminClient.from("vendor_login_attempts").upsert({
    vendor_code: vendorCode,
    failed_count: lockedOut ? 0 : nextCount,
    locked_until: lockedOut ? new Date(Date.now() + LOCKOUT_MINUTES * 60_000).toISOString() : null,
  });
}

async function clearAttempts(adminClient: Db, vendorCode: string) {
  await adminClient.from("vendor_login_attempts").delete().eq("vendor_code", vendorCode);
}

async function registerFailure(adminClient: Db, vendorCode: string, ip: string | null) {
  await recordFailure(adminClient, vendorCode);
  if (ip) await underLimit(adminClient, `signin-fail-ip:${ip}`, IP_SIGNIN_FAILURES);
}

async function handleSignin(adminClient: Db, body: any, ip: string | null) {
  const { vendor_code, password } = body ?? {};
  if (!isShortString(vendor_code) || !isShortString(password)) {
    return json({ error: GENERIC_SIGNIN_ERROR }, 400);
  }

  if (ip && !(await underLimit(adminClient, `signin-fail-ip:${ip}`, IP_SIGNIN_FAILURES, 0))) {
    return json({ error: GENERIC_SIGNIN_ERROR }, 400);
  }

  if (await isLockedOut(adminClient, vendor_code)) {
    return json({ error: GENERIC_SIGNIN_ERROR }, 400);
  }

  // role='vendor' filtering is airtight: vendor_code is null for every
  // other role by both write paths' own invariant (admin-create-vendor.ts,
  // admin-change-access.ts), and nothing else writes to profiles.
  const { data: profile } = await adminClient
    .from("profiles").select("email").eq("role", "vendor").eq("vendor_code", vendor_code).maybeSingle();

  const authClient = createClient(SUPABASE_URL, ANON_KEY);

  if (!profile?.email) {
    // Burn comparable time to a real attempt, then fail exactly the same way.
    const { error: dummyErr } = await authClient.auth.signInWithPassword({ email: DUMMY_EMAIL, password: DUMMY_PASSWORD });
    if (dummyErr?.status === 429) return json({ error: BUSY_MESSAGE }, 503);
    await registerFailure(adminClient, vendor_code, ip);
    return json({ error: GENERIC_SIGNIN_ERROR }, 400);
  }

  const { data: signIn, error: signInErr } = await authClient.auth.signInWithPassword({ email: profile.email, password });
  if (signInErr?.status === 429) return json({ error: BUSY_MESSAGE }, 503);
  if (signInErr || !signIn?.session) {
    await registerFailure(adminClient, vendor_code, ip);
    return json({ error: GENERIC_SIGNIN_ERROR }, 400);
  }

  await clearAttempts(adminClient, vendor_code);
  return json({ access_token: signIn.session.access_token, refresh_token: signIn.session.refresh_token }, 200);
}

async function handleReset(adminClient: Db, body: any, ip: string | null) {
  const { identifier, redirect_to } = body ?? {};
  if (isShortString(identifier) && isAllowedRedirect(redirect_to)) {
    const withinLimits =
      (!ip || (await underLimit(adminClient, `reset-ip:${ip}`, IP_RESETS))) &&
      (await underLimit(adminClient, `reset-code:${identifier}`, CODE_RESETS));
    if (withinLimits) {
      const { data: profile } = await adminClient
        .from("profiles").select("email").eq("role", "vendor").eq("vendor_code", identifier).maybeSingle();
      if (profile?.email) {
        const authClient = createClient(SUPABASE_URL, ANON_KEY);
        await authClient.auth.resetPasswordForEmail(profile.email, { redirectTo: redirect_to });
      }
    }
  }
  // Always the same response, whether or not identifier resolved to anything.
  return json({ ok: true, message: GENERIC_RESET_MESSAGE }, 200);
}

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
