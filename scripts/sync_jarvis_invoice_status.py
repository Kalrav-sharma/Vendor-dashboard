#!/usr/bin/env python3
"""
Syncs each uploaded invoice's Oracle SUBMISSION status from Jarvis query
597609 into po_invoice_uploads.oracle_status.

(597609 is Kalrav's own fork of the original query 594877 -- forked so he
has regenerate rights on its API key. Same SQL, same columns; scheduled to
refresh every 5 minutes same as the original.)

WHAT THIS IS NOT
----------------
This is NOT a payment sync. Query 597609 has no "paid" column -- the
closest thing, ORACLE_STATUS, records whether the invoice RECORD was
pushed into Oracle (UC's payables system), which happens within hours of
receipting and long before any money moves. ~95% of all rows read PUSHED.
Presenting that as "Paid" would tell vendors they have been paid when they
have not, so the portal labels it "In Oracle" / "Not submitted" /
"Submission failed" and this script writes it to a column named for what
it actually is. If a real payments source turns up later (Oracle AP: paid
date + UTR), it belongs in its own columns, not folded into these.

What it IS worth having: a vendor can finally see whether their invoice
reached the system that pays them, and Operations can see the ones that
failed on the way in -- which block payment outright and were previously
invisible in the portal.

HOW IT MATCHES
--------------
Jarvis is per-GRN; the portal is per-uploaded-invoice-file. One invoice
can legitimately span several GRNs (the same assumption
check-invoice-match already makes), so rows are rolled up per
(PO, invoice number) with the worst status winning:

    failed  >  not_attempted  >  pushed

A PO with three GRNs where one failed to reach Oracle is a PO that needs
attention, so the failure must not be averaged away by its two siblings.

Matching is on (po_code, normalised invoice number). Our invoice number is
OCR-extracted (match_details.extracted.invoice_number), so it can be
missing or slightly off; when it doesn't match, the script falls back to
PO-only -- but ONLY when every Jarvis row for that PO agrees on a status,
in which case the answer is the same whichever invoice it was. A PO whose
invoices disagree is left alone rather than guessed at.

CREDENTIALS
-----------
  JARVIS_API_KEY             -- Jarvis (Redash) account API key
  SUPABASE_URL               -- e.g. https://xxxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY  -- bypasses RLS; server-side only

NETWORK
-------
Jarvis is VPN / IP-allowlist gated, and GitHub's hosted runners are not on
UC's network -- so unlike every other sync here, this one runs on a
self-hosted runner inside the network (a laptop already on the VPN). See
.github/workflows/sync-jarvis-invoice-status.yml and
.github/runner-setup.md. Running it by hand from any VPN-connected machine
works identically; the workflow is only there to do it on a schedule.

Usage:
    python scripts/sync_jarvis_invoice_status.py [--dry-run] [--probe]
"""
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import requests

JARVIS_BASE = "https://jarvis.urbanclap.com"
JARVIS_QUERY_ID = 597609
REQUEST_TIMEOUT = 120
# Jarvis runs this query on its own 300s schedule, so the cached result is
# never more than ~5 minutes stale. We read that cache rather than forcing
# a refresh -- triggering our own run of a 93k-row query every 5 minutes
# would be pure waste on top of a refresh that already happened.
JARVIS_RESULTS_PATH = f"/api/queries/{JARVIS_QUERY_ID}/results.json"

# Jarvis ORACLE_STATUS -> our oracle_status. Anything unrecognised is
# skipped rather than guessed at, so a new upstream value can never be
# silently shown to a vendor as one of ours.
STATUS_MAP = {"PUSHED": "pushed", "NOT_ATTEMPTED": "not_attempted", "FAILED": "failed"}
STATUS_SEVERITY = {"pushed": 0, "not_attempted": 1, "failed": 2}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"


def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing {name} environment variable.")
    return v


def norm_invoice(value):
    """Invoice numbers are typed by humans into two different systems, so
    compare them with case, whitespace and separators removed -- the same
    reason dedupeInvoiceNumbers() exists in frontend/src/format.js."""
    if not value:
        return ""
    return re.sub(r"[\s\-_/\\.]+", "", str(value)).upper()


def fetch_jarvis_rows(key):
    req = urllib.request.Request(
        JARVIS_BASE + JARVIS_RESULTS_PATH,
        headers={"Authorization": "Key " + key, "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode(errors="replace")
        if e.code in (403, 404):
            sys.exit(
                f"Jarvis rejected the request ({e.code}): {body}\n"
                "Redash answers 404 for an unauthenticated call, so this usually means "
                "JARVIS_API_KEY is wrong/rotated rather than the query being missing."
            )
        sys.exit(f"Jarvis returned {e.code}: {body}")
    except Exception as e:
        sys.exit(f"Could not reach Jarvis ({type(e).__name__}: {e}). On UC VPN?")
    return payload["query_result"]["data"]["rows"]


def roll_up(rows):
    """-> (by_po_invoice, by_po). Worst status wins within each group."""
    by_po_invoice, by_po = {}, defaultdict(set)
    for r in rows:
        mapped = STATUS_MAP.get(str(r.get("ORACLE_STATUS") or "").strip().upper())
        if not mapped:
            continue
        po = str(r.get("PONUMBER") or "").strip()
        if not po:
            continue
        inv = norm_invoice(r.get("GRN_INVOICE_NO"))
        remark = (r.get("ORACLE_FAILURE_REMARKS") or "").strip() or None

        by_po[po].add(mapped)
        key = (po, inv)
        prev = by_po_invoice.get(key)
        if prev is None or STATUS_SEVERITY[mapped] > STATUS_SEVERITY[prev[0]]:
            by_po_invoice[key] = (mapped, remark)
    return by_po_invoice, dict(by_po)


def fetch_uploads(supabase_url, key):
    r = requests.get(
        f"{supabase_url}/rest/v1/po_invoice_uploads",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        params={"select": "id,po_code,oracle_status,match_details"},
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Fetching po_invoice_uploads failed ({r.status_code}): {r.text[:500]}")
    return r.json()


def patch_batch(supabase_url, key, ids, status, remark, now_iso):
    r = requests.patch(
        f"{supabase_url}/rest/v1/po_invoice_uploads",
        headers={
            "apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Prefer": "return=minimal",
        },
        params={"id": f"in.({','.join(str(i) for i in ids)})"},
        json={"oracle_status": status, "oracle_failure_remarks": remark, "oracle_synced_at": now_iso},
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Updating po_invoice_uploads failed ({r.status_code}): {r.text[:500]}")


def main():
    dry_run = "--dry-run" in sys.argv
    jarvis_rows = fetch_jarvis_rows(env("JARVIS_API_KEY"))
    print(f"Jarvis query {JARVIS_QUERY_ID}: {len(jarvis_rows)} row(s).")

    if "--probe" in sys.argv:
        cols = sorted({c for r in jarvis_rows[:50] for c in r})
        print("columns:", ", ".join(cols))
        return

    by_po_invoice, by_po = roll_up(jarvis_rows)
    print(f"Rolled up to {len(by_po_invoice)} (PO, invoice) group(s) across {len(by_po)} PO(s).")

    supabase_url, supabase_key = env("SUPABASE_URL").rstrip("/"), env("SUPABASE_SERVICE_ROLE_KEY")
    uploads = fetch_uploads(supabase_url, supabase_key)
    print(f"{len(uploads)} invoice upload(s) on file.")

    # (status, remark) -> [upload ids]
    updates = defaultdict(list)
    matched_exact = matched_po_only = unmatched = unchanged = 0

    for up in uploads:
        po = (up.get("po_code") or "").strip()
        inv = norm_invoice(((up.get("match_details") or {}).get("extracted") or {}).get("invoice_number"))

        hit = by_po_invoice.get((po, inv)) if inv else None
        if hit:
            matched_exact += 1
        else:
            # Fall back to PO level only when every Jarvis row for this PO
            # agrees -- then the answer doesn't depend on which invoice it was.
            statuses = by_po.get(po)
            if statuses and len(statuses) == 1:
                hit = (next(iter(statuses)), None)
                matched_po_only += 1
            else:
                unmatched += 1
                continue

        status, remark = hit
        if up.get("oracle_status") == status:
            unchanged += 1
            continue
        updates[(status, remark)].append(up["id"])

    total = sum(len(v) for v in updates.values())
    print(
        f"matched exactly: {matched_exact} · matched on PO only: {matched_po_only} · "
        f"no match: {unmatched} · already current: {unchanged} · to update: {total}"
    )

    if dry_run:
        for (status, remark), ids in updates.items():
            print(f"  [dry-run] {status:<14} x{len(ids)}" + (f"  ({remark[:50]})" if remark else ""))
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    for (status, remark), ids in updates.items():
        for i in range(0, len(ids), 200):
            patch_batch(supabase_url, supabase_key, ids[i:i + 200], status, remark, now_iso)
    print(f"Updated {total} invoice upload(s).")


if __name__ == "__main__":
    main()
