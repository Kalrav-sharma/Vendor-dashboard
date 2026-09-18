#!/usr/bin/env python3
"""
Pulls Jarvis query 562880 (SERVICEABILITYRULES_DP__VIEW) and wholesale-
replaces public.last_mile_sla_rules in Supabase.

RUN MANUALLY, ON THE VPN, ROUGHLY WEEKLY -- deliberately not scheduled.
SLA rules change rarely, so a self-hosted-runner workflow isn't worth the
infrastructure for this one piece. Jarvis is IP-gated at Cloudflare's
edge, confirmed 2026-09-18: a GitHub-hosted runner hitting this exact API
got Cloudflare's own "Attention Required" block page, not an auth error.
The same key worked fine from a local VPN-connected machine in that same
test -- so this script simply cannot run anywhere except a machine on
the VPN, scheduled or not.

This script does not touch scripts/last_mile_lib/ at all. The daily
Uniware pull (sync_last_mile_daily.py, GitHub-hosted, no VPN needed) is
the one that reads last_mile_sla_rules back out and materialises it into
the local CSV last_mile_lib/sla.py's SlaRules() already expects -- so
running THIS script has no effect until the next daily pull runs
afterward and picks up the refreshed table.

CREDENTIALS
  JARVIS_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  (same three names as every other sync script here -- see
  requirements.txt / the GitHub Actions secrets already configured for
  the other last-mile scripts. Set these as real environment variables
  before running, never hardcoded here.)

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
                  "A Cloudflare 'Attention Required' page here almost always "
                  "means this is NOT running on the VPN -- check your connection.")
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
