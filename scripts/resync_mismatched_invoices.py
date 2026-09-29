"""Targeted daily sweep: re-syncs and re-checks every invoice currently
showing a reconciliation mismatch, so a Uniware correction made AFTER a
PO/GRN already settled (a corrected vendor invoice number, say) surfaces
on the portal without anyone needing to manually force-refresh + click
Re-check (see Kalrav's PKLU/PO2627/0209 case, 2026-09-29).

Deliberately NOT a blanket re-sync of every settled PO -- see
sync_to_supabase.py's is_settled() cost-control design and the recent
Supabase quota work. This only force-refreshes the (small) set of PO
codes that currently have a mismatched invoice, by shelling out to
sync_to_supabase.py's own --force flag rather than re-implementing any of
its Uniware-fetching logic, then re-runs the same check-invoice-match
Edge Function the "Re-check" button calls -- authenticating as a trusted
system caller with the service_role key (see that function's
isSystemCaller branch) rather than a user's session, since this runs
unattended.

Some mismatches will never resolve this way (a genuine qty/value overage
needing a vendor credit note, not a data fix) -- re-checking those again
each run just re-confirms the same mismatch, same as a human clicking
Re-check repeatedly would. That's an accepted, bounded cost: one Claude
OCR re-extraction per currently-mismatched invoice, once a day, not per
settled PO in the whole system.

Required environment variables:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  -- same as every other sync script
  UNIWARE_USERNAME, UNIWARE_PASSWORD        -- passed through to the
                                               sync_to_supabase.py subprocess

Run with: python3 scripts/resync_mismatched_invoices.py
"""
import os
import subprocess
import sys
import time

import requests

REQUEST_TIMEOUT = 60
RECHECK_PAUSE_SECONDS = 0.5  # be polite to check-invoice-match's own Anthropic call


def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing {name} environment variable.")
    return v


def supabase_config():
    return env("SUPABASE_URL").rstrip("/"), env("SUPABASE_SERVICE_ROLE_KEY")


def fetch_mismatched_invoices(supabase_url, key):
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(
        f"{supabase_url}/rest/v1/po_invoice_uploads",
        headers=headers,
        params={"select": "id,po_code", "match_status": "eq.mismatch"},
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Fetching mismatched invoices failed ({r.status_code}): {r.text[:500]}")
    return r.json()


def force_refresh(po_codes):
    """Shells out to sync_to_supabase.py --force <codes> -- the single
    source of truth for talking to Uniware, never re-implemented here."""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_to_supabase.py")
    result = subprocess.run(
        [sys.executable, script_path, "--force", *po_codes],
        check=False,
    )
    if result.returncode != 0:
        sys.exit(f"sync_to_supabase.py --force exited {result.returncode} -- aborting re-checks.")


def recheck_invoice(supabase_url, key, upload_id):
    try:
        r = requests.post(
            f"{supabase_url}/functions/v1/check-invoice-match",
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"upload_id": upload_id},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        print(f"  WARN: re-check request failed for upload {upload_id}: {type(e).__name__}")
        return None
    if not r.ok:
        print(f"  WARN: re-check failed for upload {upload_id} ({r.status_code}): {r.text[:300]}")
        return None
    return r.json()


def main():
    supabase_url, supabase_key = supabase_config()

    mismatched = fetch_mismatched_invoices(supabase_url, supabase_key)
    if not mismatched:
        print("No mismatched invoices on file -- nothing to resync.")
        return

    po_codes = sorted({row["po_code"] for row in mismatched if row.get("po_code")})
    print(f"{len(mismatched)} mismatched invoice(s) across {len(po_codes)} PO(s): {', '.join(po_codes)}")

    print("Force-refreshing these POs from Uniware...")
    force_refresh(po_codes)

    print(f"Re-checking {len(mismatched)} invoice(s)...")
    resolved = 0
    for row in mismatched:
        result = recheck_invoice(supabase_url, supabase_key, row["id"])
        if result and result.get("match_status") == "matched":
            resolved += 1
            print(f"  upload {row['id']} ({row['po_code']}): now matched")
        time.sleep(RECHECK_PAUSE_SECONDS)

    print(f"Done. {resolved}/{len(mismatched)} previously-mismatched invoice(s) resolved this run.")


if __name__ == "__main__":
    main()
