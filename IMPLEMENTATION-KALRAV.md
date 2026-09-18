# Implementation tasks — Kalrav

Pull `main` first.

---

## Task 1 — SLA rules: done, but manually, not via your runner

**Status: built and live as of 2026-09-19.** The design below described a
scheduled self-hosted-runner workflow (`sync-last-mile-sla.yml`) for
pulling real `SERVICEABILITYRULES_DP` rules. That workflow was never
built. Instead, since these rules change rarely (roughly weekly), Praneeth
is running the pull **by hand, from his own laptop, on the VPN** — no
runner, no schedule, no infrastructure. Everything downstream (the table,
the script, the daily job's CSV materialisation) is real and pushed.

**What this means for you: nothing to build here.** If you're curious how
it works day to day, or want to move it to a schedule later, the original
design write-up is kept below for reference — but the self-hosted-runner
check that used to be "Task 1" is only relevant if you decide you want
this automated instead of manual. It isn't blocking anything right now.

---

## Reference — the original automated design (not built; kept for context)

### Why this exists

The Last Mile Tracking page's alerts currently flag a shipment as
`BREACHED` against a **generic assumed promise** (a fixed default number
of days), not your actual `SERVICEABILITYRULES_DP` lane-by-lane SLAs.
Every promise on the page right now reads `promise_source: ASSUMED`.
This task replaces that with the real rules, sourced from Jarvis query
**562880** (`SERVICEABILITYRULES_DP__VIEW`).

**The good news: none of the analysis code changes.** `sla.py`'s
`SlaRules` class, `alerts.py`'s `BREACHED` flag, and every downstream
calculation are already built and already running — they're just reading
an empty stub file
(`scripts/last_mile_lib/reference/serviceability_rules_active.csv`,
header-only, zero data rows). This task's whole job is getting real rows
into that pipeline. Nothing in `scripts/last_mile_lib/` needs editing.

### Why this can't just run on GitHub Actions like everything else

Confirmed empirically, not assumed: a GitHub-hosted runner hitting
Jarvis's real API gets Cloudflare's own WAF block page back
(`<title>Attention Required! | Cloudflare</title>`), not an auth error —
this is blocked at the network edge before it ever reaches the
application. The same `JARVIS_API_KEY` worked fine from a local
VPN-connected machine in the same test. So: the *pull from Jarvis* must
happen on VPN-reachable compute (your runner). The *rest of the
pipeline* — the daily Uniware pull, the hourly courier polling — stays
exactly where it is, on GitHub-hosted runners, because Uniware and the
couriers are reachable from the open internet. Only this one piece needs
your laptop.

The design below keeps that split clean: your runner's only job is
"Jarvis → Supabase." The GitHub-hosted daily job's only new job is
"Supabase → local file the existing code already reads." Neither side
needs to know how the other one works.

### Step 1 — New Supabase table

Paste into SQL Editor → Run (append to `supabase/schema.sql` too, so a
future full re-apply doesn't drop it):

```sql
-- Real SERVICEABILITYRULES_DP rows, synced from Jarvis query 562880 by
-- scripts/sync_last_mile_sla_rules.py (runs on the VPN self-hosted
-- runner -- see .github/workflows/sync-last-mile-sla.yml). Wholesale
-- replaced each run (delete-then-insert), same convention as
-- mm_rate_card. Read by sync_last_mile_daily.py (GitHub-hosted, no VPN
-- needed for THIS read) to materialise the local CSV
-- scripts/last_mile_lib/sla.py's SlaRules() already expects.
create table if not exists public.last_mile_sla_rules (
  id bigserial primary key,
  pincode text,
  city text,
  warehouse text,
  lsp_partner text,
  slacode text,
  is_active boolean not null default true,
  synced_at timestamptz not null default now()
);
create index if not exists idx_last_mile_sla_rules_pincode on public.last_mile_sla_rules (pincode);
create index if not exists idx_last_mile_sla_rules_city on public.last_mile_sla_rules (city);

alter table public.last_mile_sla_rules enable row level security;
drop policy if exists last_mile_sla_rules_select on public.last_mile_sla_rules;
create policy last_mile_sla_rules_select on public.last_mile_sla_rules
  for select using (public.is_internal_staff());
```

### Step 2 — New workflow: `.github/workflows/sync-last-mile-sla.yml`

Runs once daily, comfortably before the last-mile daily pull (04:10 UTC)
so fresh rules are in place before that day's watchlist build. Mirrors
the preflight-VPN-check pattern the old Jarvis invoice-status workflow
used, so a closed laptop or dead VPN ends the run cleanly green instead
of red.

```yaml
name: Sync Last Mile SLA rules

# Runs on the VPN self-hosted runner -- Jarvis is IP-gated at Cloudflare's
# edge, confirmed 2026-09-18 (a GitHub-hosted runner got Cloudflare's own
# "Attention Required" block page, not an auth error). See
# IMPLEMENTATION-KALRAV.md Task 2 for the full story.
#
# Daily, ahead of the 04:10 UTC last-mile Uniware pull, so that day's
# watchlist build has fresh rules. If the laptop is off or off-VPN, this
# ends cleanly green with a notice -- not a failure -- and the daily job
# just keeps using whatever rules synced last.
on:
  schedule:
    - cron: '30 3 * * *'   # 09:00 IST, 40 min before the last-mile daily pull
  workflow_dispatch: {}

permissions:
  contents: read

concurrency:
  group: sync-last-mile-sla-lock
  cancel-in-progress: false

jobs:
  sync:
    runs-on: [self-hosted, vpn]
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4

      - name: Check Jarvis is reachable (is the VPN up?)
        id: preflight
        shell: bash
        run: |
          code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://jarvis.urbanclap.com/ || echo 000)
          if [ "$code" = "000" ]; then
            echo "reachable=false" >> "$GITHUB_OUTPUT"
            echo "::notice::Jarvis unreachable (VPN likely down) -- skipping this run, SLA rules not refreshed."
          else
            echo "reachable=true" >> "$GITHUB_OUTPUT"
          fi

      - uses: actions/setup-python@v5
        if: steps.preflight.outputs.reachable == 'true'
        with:
          python-version: '3.11'

      - run: pip install -r requirements.txt
        if: steps.preflight.outputs.reachable == 'true'

      - run: python scripts/sync_last_mile_sla_rules.py
        if: steps.preflight.outputs.reachable == 'true'
        env:
          JARVIS_API_KEY: ${{ secrets.JARVIS_API_KEY }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
```

`JARVIS_API_KEY` is already a repo secret (added 2026-09-18 for the
reachability test). Nothing new to add there.

### Step 3 — New script: `scripts/sync_last_mile_sla_rules.py`

Pulls query 562880, writes into `last_mile_sla_rules`. Same Redash
`Authorization: Key` pattern already proven twice this project (the
reachability test, and the earlier — now removed — invoice-status sync).

```python
#!/usr/bin/env python3
"""
Pulls Jarvis query 562880 (SERVICEABILITYRULES_DP__VIEW) and wholesale-
replaces public.last_mile_sla_rules in Supabase.

Runs on the VPN self-hosted runner ONLY -- Jarvis is IP-gated at
Cloudflare's edge, confirmed 2026-09-18 against a GitHub-hosted runner.
See IMPLEMENTATION-KALRAV.md Task 2.

This script does not touch scripts/last_mile_lib/ at all. The daily
Uniware pull (sync_last_mile_daily.py, GitHub-hosted, no VPN needed) is
the one that reads last_mile_sla_rules back out and materialises it into
the local CSV last_mile_lib/sla.py's SlaRules() already expects.

CREDENTIALS
  JARVIS_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Usage:
    python scripts/sync_last_mile_sla_rules.py [--dry-run]
"""
import json
import os
import sys
import urllib.request

import requests

JARVIS_BASE = "https://jarvis.urbanclap.com"
JARVIS_QUERY_ID = 562880
REQUEST_TIMEOUT = 120
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"


def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing {name} environment variable.")
    return v


def fetch_jarvis_rows(key):
    req = urllib.request.Request(
        f"{JARVIS_BASE}/api/queries/{JARVIS_QUERY_ID}/results.json",
        headers={"Authorization": "Key " + key, "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode(errors="replace")
        sys.exit(f"Jarvis returned {e.code}: {body}\n"
                  "A Cloudflare 'Attention Required' page here means this "
                  "did NOT run on the VPN runner -- check runs-on.")
    except Exception as e:
        sys.exit(f"Could not reach Jarvis ({type(e).__name__}: {e}). On the VPN?")
    return payload["query_result"]["data"]["rows"]


def to_row(r):
    return {
        "pincode": (r.get("PINCODE") or "").strip() or None,
        "city": (r.get("CITY") or "").strip() or None,
        "warehouse": (r.get("WAREHOUSE") or "").strip() or None,
        "lsp_partner": (r.get("LSPPARTNER") or "").strip() or None,
        "slacode": (r.get("SLACODE") or "").strip() or None,
        "is_active": str(r.get("ISACTIVE")).strip().lower() not in ("false", "0", "no"),
    }


def main():
    dry_run = "--dry-run" in sys.argv
    rows = fetch_jarvis_rows(env("JARVIS_API_KEY"))
    print(f"Jarvis query {JARVIS_QUERY_ID}: {len(rows)} row(s).")

    # Refuses to wipe a good table on an ambiguous empty result -- same
    # discipline as sync_mm_rate_card.py's replace_rows(): a transient
    # Jarvis fetch/parse failure looks identical to "genuinely zero active
    # rules today", and leaving stale (but real) data one run longer beats
    # deleting everything on that ambiguity.
    if not rows:
        sys.exit("Jarvis returned zero rows -- aborting without touching "
                  "last_mile_sla_rules (could be a fetch failure, not a real empty table).")

    out_rows = [to_row(r) for r in rows]
    active = sum(1 for r in out_rows if r["is_active"])
    print(f"  active: {active} | inactive: {len(out_rows) - active}")

    if dry_run:
        print("[dry-run] nothing written.")
        return

    url = env("SUPABASE_URL").rstrip("/")
    key = env("SUPABASE_SERVICE_ROLE_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    # Wholesale replace -- same convention and same delete-filter idiom as
    # sync_mm_rate_card.py's replace_rows() (not.is.null on the primary
    # key matches every row). Delete first, then insert, so a failed run
    # never leaves half-old/half-new rows.
    r = requests.delete(f"{url}/rest/v1/last_mile_sla_rules", headers=headers,
                        params={"id": "not.is.null"}, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        sys.exit(f"Clearing last_mile_sla_rules failed ({r.status_code}): {r.text[:400]}")

    for i in range(0, len(out_rows), 500):
        r = requests.post(f"{url}/rest/v1/last_mile_sla_rules", headers={**headers, "Prefer": "return=minimal"},
                          json=out_rows[i:i + 500], timeout=REQUEST_TIMEOUT)
        if not r.ok:
            sys.exit(f"Inserting last_mile_sla_rules failed ({r.status_code}): {r.text[:400]}")

    print(f"Wrote {len(out_rows)} row(s) into last_mile_sla_rules.")


if __name__ == "__main__":
    main()
```

### Step 4 — Teach the daily job to materialise the CSV

Add this to `scripts/sync_last_mile_daily.py`, called once near the top
of `main()`, **before** `build_watchlist_rows()` runs (which is what
constructs `SlaRules()`):

```python
def refresh_sla_rules_csv(supabase_url, key):
    """Pull public.last_mile_sla_rules and overwrite the local CSV
    scripts/last_mile_lib/sla.py's SlaRules() reads. Runs on GitHub-hosted
    -- no VPN needed, this is a plain Supabase read, not a Jarvis call.

    If the table is empty (the VPN runner has never synced, or is down),
    this writes a header-only file, same as the stub it's replacing --
    every promise falls through to ASSUMED, exactly like it does today.
    Nothing here can make the daily pull fail because SLA rules aren't
    ready yet.
    """
    import csv as csv_mod
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "last_mile_lib", "reference", "serviceability_rules_active.csv")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows = []
    try:
        r = requests.get(f"{supabase_url}/rest/v1/last_mile_sla_rules",
                         headers=headers, params={"select": "*"}, timeout=REQUEST_TIMEOUT)
        if r.ok:
            rows = r.json()
    except Exception as e:
        print(f"WARN: could not read last_mile_sla_rules ({e}) -- using ASSUMED promises.", file=sys.stderr)

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.writer(f)
        w.writerow(["PINCODE", "CITY", "WAREHOUSE", "LSPPARTNER", "SLACODE", "ISACTIVE"])
        for r in rows:
            w.writerow([r.get("pincode") or "", r.get("city") or "", r.get("warehouse") or "",
                       r.get("lsp_partner") or "", r.get("slacode") or "",
                       "true" if r.get("is_active") else "false"])
    print(f"SLA rules CSV: {len(rows)} row(s) (real rules if >0, ASSUMED fallback if 0).")
```

`supabase_config()` currently gets called near the BOTTOM of `main()`
(line 371, after the watchlist is already built) — it needs to move up
so `refresh_sla_rules_csv()` can use it before `build_watchlist_rows()`
runs. Here's the exact current `main()` and what it becomes, so there's
nothing to interpret:

**Current** (`scripts/sync_last_mile_daily.py`, inside `main()`):
```python
    print(f"{len(all_rows)} order-item row(s) across {len(facilities)} facilities.")
    # NOT named `watchlist` -- that shadows the imported last_mile_lib.watchlist
    # module, which build_watchlist_rows() itself still needs to call.
    watchlist_rows = build_watchlist_rows(all_rows)
    print(f"Collapsed to {len(watchlist_rows)} shipment(s) in scope.")
```
...
```python
    if dry_run:
        print("[dry-run] nothing written.")
        return

    supabase_url, key = supabase_config()
    upsert_watchlist(supabase_url, key, watchlist_rows)
    print(f"Upserted {len(watchlist_rows)} row(s) into last_mile_watchlist.")
```

**Becomes:**
```python
    print(f"{len(all_rows)} order-item row(s) across {len(facilities)} facilities.")
    supabase_url, key = supabase_config()
    refresh_sla_rules_csv(supabase_url, key)
    # NOT named `watchlist` -- that shadows the imported last_mile_lib.watchlist
    # module, which build_watchlist_rows() itself still needs to call.
    watchlist_rows = build_watchlist_rows(all_rows)
    print(f"Collapsed to {len(watchlist_rows)} shipment(s) in scope.")
```
...
```python
    if dry_run:
        print("[dry-run] nothing written.")
        return

    upsert_watchlist(supabase_url, key, watchlist_rows)
    print(f"Upserted {len(watchlist_rows)} row(s) into last_mile_watchlist.")
```

In short: `supabase_url, key = supabase_config()` moves from just before
`upsert_watchlist(...)` to just before `build_watchlist_rows(...)`, and
`refresh_sla_rules_csv(supabase_url, key)` gets called right after the
move, once, before the collapse.

### What actually happened instead (real, as of 2026-09-19)

No `sync-last-mile-sla.yml` workflow exists. `scripts/sync_last_mile_sla_rules.py`
is run by hand, on the VPN, whenever someone remembers to (roughly
weekly). Steps 1, 3 and 4 (the table, the script, the daily job's CSV
step) are exactly as designed above and are live. Step 2 (the scheduled
workflow) was skipped — the manual run replaces it.

If you ever want this on a schedule instead of manual, Task 1's original
runner-check plus Step 2's workflow YAML above is the whole remaining
gap — nothing else changes.

### One thing to decide, not build

`awb_pattern_ok`, `promise_slacode`, and `promise_days` are already
wired through end to end and will start reflecting real rules
automatically — no extra work. But if any lane in this query has no
matching rule, `SlaRules.lookup()` already falls through to `ASSUMED`
for that lane specifically (not the whole table) — that's existing,
correct behaviour, not a gap to fix.
