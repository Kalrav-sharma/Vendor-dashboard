// Called by Supabase pg_cron every 20 min (supabase/2026-10-09-last-mile-watchdog-cron.sql) to
// start GitHub's sync-last-mile-watchdog.yml, which refreshes whichever Last Mile leg (daily
// intake / hourly tracking) is stale and does nothing if both are fresh.
//
// Why not GitHub's own cron: GitHub silently drops most scheduled ticks for this repo (2026-10-06
// to 10-08: the hourly tracking leg ran 2-3 times a day instead of 14, the watchdog's own
// schedule a handful of times), while an on-demand workflow_dispatch always starts at once.
//
// Uses the GITHUB_DISPATCH_TOKEN secret trigger-inventory-sync already has, so no token is ever
// pasted into SQL. Deployed with "Verify JWT" OFF -- pg_cron calls it with no user session, and
// the project's publishable key isn't a JWT. That's safe because this can do exactly one thing,
// start the watchdog, and refuses if the watchdog already started in the last 10 minutes: the
// worst an outside caller could cause is one extra freshness check per 10 minutes. It reads and
// writes no data.
//
// Deploy: Supabase dashboard > Edge Functions > deploy new function "dispatch-last-mile-watchdog",
// then turn OFF "Enforce JWT verification" (Verify JWT) for it.

const GITHUB_TOKEN = Deno.env.get("GITHUB_DISPATCH_TOKEN");
const REPO = "Kalrav-sharma/Vendor-dashboard";
const WORKFLOW = "sync-last-mile-watchdog.yml";
const MIN_GAP_MS = 10 * 60 * 1000;

Deno.serve(async () => {
  if (!GITHUB_TOKEN) return json({ error: "GITHUB_DISPATCH_TOKEN secret is missing" }, 500);
  try {
    // Rate limit: one watchdog start per 10 minutes, whoever asks.
    const recent = await gh(`/actions/workflows/${WORKFLOW}/runs?per_page=1`);
    if (!recent.ok) return json({ error: `GitHub API ${recent.status}: ${(await recent.text()).slice(0, 200)}` }, 502);
    const last = (await recent.json()).workflow_runs?.[0];
    if (last && Date.now() - new Date(last.created_at).getTime() < MIN_GAP_MS) {
      return json({ skipped: true, reason: `watchdog already started at ${last.created_at}` });
    }
    const res = await gh(`/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: "main" }),
    });
    if (res.status !== 204) return json({ error: `GitHub dispatch ${res.status}: ${(await res.text()).slice(0, 200)}` }, 502);
    return json({ dispatched: true });
  } catch (e) {
    return json({ error: `Unexpected error: ${e instanceof Error ? e.message : String(e)}` }, 500);
  }
});

// Same GitHub call shape as trigger-inventory-sync.
function gh(path: string, init: RequestInit = {}) {
  return fetch(`https://api.github.com/repos/${REPO}${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${GITHUB_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "vendor-portal-dispatch-last-mile-watchdog",
      "Content-Type": "application/json",
    },
  });
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
