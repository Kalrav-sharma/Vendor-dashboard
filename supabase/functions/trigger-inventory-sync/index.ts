// Internal-staff Edge Function behind the Logistics Health Card's "Sync now" button
// (2026-10-05, per Anish). GitHub's */30 schedules for the inventory syncs get throttled
// for hours at a time, while a workflow_dispatch run starts within a minute -- so this
// dispatches them on demand:
//   sync-uniware-inventory.yml  RO stock snapshot; on success it chains sync-sop-inventory.yml
//                               (S&OP / DOI tables) via workflow_run
//   sync-spares.yml             Spares sheet master + Uniware good/bad
//
// body: { action: "trigger" }
//   -> { started: [wf], alreadyRunning: [wf], since: ISO }
//   A workflow with a run already queued/in progress is left alone, so repeat clicks
//   don't stack runs.
// body: { action: "status", since: ISO }
//   -> { runs: [{ wf, status, conclusion, html_url } | { wf, status: "pending" }] }
//   Latest run of each watched workflow created at/after `since` (less a minute of slack).
//   sync-sop-inventory reads "pending" until the Uniware run finishes and chains it.
//
// Caller must be internal staff (admin / management / operations / finance -- same set
// as public.is_internal_staff()).
//
// Deploy: Supabase dashboard > Edge Functions > deploy new function "trigger-inventory-sync".
// Required secrets: GITHUB_DISPATCH_TOKEN (classic PAT with "repo" scope from a collaborator
// with write access to the repo). SUPABASE_URL / SUPABASE_ANON_KEY are auto-injected.

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const GITHUB_TOKEN = Deno.env.get("GITHUB_DISPATCH_TOKEN");

const REPO = "Kalrav-sharma/Vendor-dashboard";
const WORKFLOWS = ["sync-uniware-inventory.yml", "sync-spares.yml"];
const WATCH = [...WORKFLOWS, "sync-sop-inventory.yml"];
const INTERNAL_ROLES = new Set(["admin", "management", "operations", "finance"]);

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
    if (profileErr || !callerProfile || !INTERNAL_ROLES.has(callerProfile.role)) {
      return json({ error: "Only internal staff can start a sync" }, 403);
    }

    if (!GITHUB_TOKEN) {
      return json({ error: "Sync isn't set up yet: GITHUB_DISPATCH_TOKEN secret is missing" }, 500);
    }

    const body = await req.json().catch(() => ({}));

    if (body.action === "trigger") {
      // An already-running run predates "now", so `since` moves back to its start --
      // otherwise the status poll would never find it.
      let sinceMs = Date.now();
      const started: string[] = [];
      const alreadyRunning: string[] = [];
      for (const wf of WORKFLOWS) {
        const active = (await latestRuns(wf, 5)).filter((r) => r.status === "queued" || r.status === "in_progress");
        if (active.length) {
          alreadyRunning.push(wf);
          sinceMs = Math.min(sinceMs, ...active.map((r) => Date.parse(r.created_at)));
          continue;
        }
        const res = await gh(`/actions/workflows/${wf}/dispatches`, {
          method: "POST",
          body: JSON.stringify({ ref: "main" }),
        });
        if (res.status !== 204) {
          return json({ error: `GitHub refused to start ${wf} (${res.status}): ${(await res.text()).slice(0, 200)}` }, 502);
        }
        started.push(wf);
      }
      return json({ started, alreadyRunning, since: new Date(sinceMs).toISOString() }, 200);
    }

    if (body.action === "status") {
      const sinceMs = Date.parse(body.since);
      if (Number.isNaN(sinceMs)) {
        return json({ error: "status needs a valid `since`" }, 400);
      }
      const runs = [];
      let uniwareDoneMs: number | null = null;
      for (const wf of WATCH) {
        // sync-sop-inventory only counts once it started after the Uniware run finished --
        // a scheduled run that began earlier would still be reading the old snapshot.
        let cutoff = sinceMs - 60_000;
        if (wf === "sync-sop-inventory.yml") {
          if (uniwareDoneMs == null) {
            runs.push({ wf, status: "pending" });
            continue;
          }
          cutoff = uniwareDoneMs - 5_000;
        }
        const run = (await latestRuns(wf, 5)).find((r) => Date.parse(r.created_at) >= cutoff);
        if (wf === "sync-uniware-inventory.yml" && run?.status === "completed") {
          uniwareDoneMs = Date.parse(run.updated_at);
        }
        runs.push(run
          ? { wf, status: run.status, conclusion: run.conclusion, html_url: run.html_url }
          : { wf, status: "pending" });
      }
      return json({ runs }, 200);
    }

    return json({ error: "action must be 'trigger' or 'status'" }, 400);
  } catch (err) {
    return json({ error: (err as Error).message }, 500);
  }
});

async function gh(path: string, init: RequestInit = {}) {
  return await fetch(`https://api.github.com/repos/${REPO}${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${GITHUB_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "vendor-portal-trigger-inventory-sync",
    },
  });
}

async function latestRuns(wf: string, n: number) {
  const res = await gh(`/actions/workflows/${wf}/runs?per_page=${n}`);
  if (!res.ok) {
    throw new Error(`GitHub run lookup for ${wf} failed (${res.status}): ${(await res.text()).slice(0, 200)}`);
  }
  return (await res.json()).workflow_runs as Array<{
    status: string; conclusion: string | null; html_url: string; created_at: string; updated_at: string;
  }>;
}

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
