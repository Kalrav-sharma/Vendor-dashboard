#!/usr/bin/env python3
"""
Live on-hand inventory, straight from Uniware.

This is the ONLY script that talks to Uniware about inventory. It publishes a
raw per-facility x per-SKU snapshot into sop_uniware_inventory, and every other
S&OP script derives its own view from that table:

    Uniware -> sync_uniware_inventory.py -> sop_uniware_inventory
                                                  |
                sync_sop_inventory.py  /  sync_sop_dispatch_plan.py  /  sync_sop_po_fulfillment.py

One writer, many readers, on purpose. Before this, three separate scripts each
parsed on-hand out of the WH-Channel-SKU sheet's "Current Inventory" tab with
slightly different rules, so the same warehouse could show different stock on
different tabs. Going through one table makes that impossible -- and means we
fire the (slow) Uniware export jobs once per cycle instead of three times.

HOW UNIWARE SERVES INVENTORY: there is no synchronous "give me stock" endpoint.
The only way is an async export job -- create, poll until it completes, then
download a CSV from the returned URL -- and the job is scoped to ONE facility
via a `Facility:` request header, so this loops ~28 facilities sequentially and
takes a few minutes. That is normal, not a hang. This whole flow (including the
misspelled `exportColums` key, which the API requires, and the ['All'] sentinel,
because enumerated column codes are rejected with INVALID_EXPORT_JOB_COLUMN) is
a direct port of ~/.claude/scripts/sync-warehouse-inventory-to-sheet.js, which
has been running this same configuration unattended against these same
facilities and SKUs.

ON-HAND is the CSV's "Inventory" column. Blocked / bad / quarantined /
in-transit / open-purchase columns are deliberately ignored -- same choice the
JS script makes, so the portal and the sheet agree on what "on hand" means.

Credentials, as GitHub Actions repo secrets (already provisioned for
refresh.yml -- no new secrets, but this workflow has to be granted them too):
  UNIWARE_USERNAME
  UNIWARE_PASSWORD
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""
import csv
import io
import os
import sys
import time

import requests

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import (  # noqa: E402
    ALL_UNIWARE_FACILITIES, REQUEST_TIMEOUT, UNIWARE_SKU_MAP, replace_by_filter, supabase_config,
)

UNIWARE_BASE_URL = "https://urbanclap.unicommerce.com"

AUTH_RETRY_ATTEMPTS = 3
AUTH_RETRY_BACKOFF_SECONDS = 5      # doubles each attempt: 5s, 10s
# 4 attempts with a generous linear backoff (15s, 30s, 45s), not the JS's 3x4s: Uniware's export
# queue was observed failing every job for a ~3-minute stretch on 2026-09-16 and recovering on its
# own, which a tight retry sits right through. Each attempt also spends up to MAX_POLLS waiting, so
# a facility can take a few minutes before it's declared dead.
FACILITY_RETRY_ATTEMPTS = 4
FACILITY_RETRY_BACKOFF_SECONDS = 15
POLL_INTERVAL_SECONDS = 3
MAX_POLLS = 60                      # 3 minutes; jobs normally finish in 1-2 polls

EXPORT_BODY = {
    "exportJobTypeName": "Inventory Snapshot",
    "exportColums": ["All"],  # NOT a typo on our side -- the API spells it this way
    "exportFilters": [],
    "frequency": "ONETIME",
}


def get_uniware_token():
    """Password-grant OAuth, same as sync_to_supabase.py (the repo's other Uniware caller).
    Retried because this endpoint intermittently drops connections."""
    username = os.environ.get("UNIWARE_USERNAME")
    password = os.environ.get("UNIWARE_PASSWORD")
    if not username or not password:
        sys.exit("Missing UNIWARE_USERNAME / UNIWARE_PASSWORD environment variables.")
    last_error = None
    for attempt in range(1, AUTH_RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(
                f"{UNIWARE_BASE_URL}/oauth/token",
                params={
                    "grant_type": "password",
                    "client_id": "my-trusted-client",
                    "username": username,
                    "password": password,
                },
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            last_error = f"Uniware auth request failed (network error): {type(e).__name__}"
            print(f"WARN: {last_error} (attempt {attempt}/{AUTH_RETRY_ATTEMPTS})", file=sys.stderr)
            if attempt < AUTH_RETRY_ATTEMPTS:
                time.sleep(AUTH_RETRY_BACKOFF_SECONDS * attempt)
            continue
        if not resp.ok:
            # Deliberately fatal rather than retried, even on a 5xx: Uniware answers bad
            # credentials with HTTP 500 too (verified 2026-09-16), so a retry loop here can't tell
            # "server hiccup" from "wrong password" and would hammer the login endpoint with bad
            # credentials -- risking a lockout that would take out every other Uniware automation.
            # A failed run is cheap by comparison: the previous snapshot stays live and the next
            # cron is 30 minutes away.
            sys.exit(f"Uniware auth failed with HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        if "access_token" not in data:
            sys.exit(f"Uniware authentication failed: {data}")
        return data["access_token"]
    sys.exit(last_error)


def uniware_headers(token, facility_code):
    return {
        "Content-Type": "application/json",
        "Authorization": f"bearer {token}",
        "Facility": facility_code,
    }


def run_export_job(token, facility):
    """Create an Inventory Snapshot export for one facility, wait for it, return the CSV text."""
    headers = uniware_headers(token, facility)
    created = requests.post(f"{UNIWARE_BASE_URL}/services/rest/v1/export/job/create",
                             headers=headers, json=EXPORT_BODY, timeout=REQUEST_TIMEOUT)
    if not created.ok:
        raise RuntimeError(f"create export failed (HTTP {created.status_code}): {created.text[:300]}")
    job_code = created.json().get("jobCode")
    if not job_code:
        raise RuntimeError(f"create export returned no jobCode: {created.json()}")

    for _ in range(MAX_POLLS):
        status = requests.post(f"{UNIWARE_BASE_URL}/services/rest/v1/export/job/status",
                                headers=headers, json={"jobCode": job_code}, timeout=REQUEST_TIMEOUT)
        if not status.ok:
            raise RuntimeError(f"poll failed (HTTP {status.status_code}): {status.text[:300]}")
        payload = status.json()
        state = payload.get("status")
        if state in ("FAILED", "ERROR"):
            raise RuntimeError(f"export job failed: {payload}")
        # Both conditions matter: the job can report COMPLETE a moment before filePath is
        # populated, and downloading "" would look like an empty (zero-stock) facility.
        if state in ("COMPLETE", "SUCCESSFUL") and payload.get("filePath"):
            # The CSV lives on public CloudFront -- sending the bearer token here 403s.
            download = requests.get(payload["filePath"], timeout=REQUEST_TIMEOUT)
            if not download.ok:
                raise RuntimeError(f"CSV download failed (HTTP {download.status_code})")
            return download.text
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"export job timed out after {MAX_POLLS * POLL_INTERVAL_SECONDS}s")


def parse_facility_inventory(csv_text, facility):
    """CSV -> {our SKU name: on-hand qty}, keeping only the 6 SKUs we track.

    Duplicate rows for one SKU are summed defensively (none observed in practice). A SKU absent
    from the export genuinely means zero stock of it at that facility, so it's left at 0."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = [r for r in reader if r]
    if not rows:
        raise RuntimeError("export CSV was empty")
    header = rows[0]
    try:
        sku_idx = header.index("Item SkuCode")
        inv_idx = header.index("Inventory")
    except ValueError:
        raise RuntimeError(f'unexpected CSV header (missing "Item SkuCode"/"Inventory"): {header[:12]}')

    by_sku = {sku: 0.0 for sku in UNIWARE_SKU_MAP.values()}
    matched = 0
    for row in rows[1:]:
        if sku_idx >= len(row) or inv_idx >= len(row):
            continue
        sku = UNIWARE_SKU_MAP.get(row[sku_idx])  # exact match -- casing differs between codes
        if not sku:
            continue
        try:
            by_sku[sku] += float(row[inv_idx] or 0)
        except ValueError:
            pass
        matched += 1
    if matched == 0:
        print(f"WARNING: {facility} returned {len(rows) - 1} CSV rows but none of our 6 SKUs -- "
              f"treating the whole facility as zero stock.", file=sys.stderr)
    return by_sku


def fetch_facility_with_retries(token, facility):
    last_error = None
    for attempt in range(1, FACILITY_RETRY_ATTEMPTS + 1):
        try:
            return parse_facility_inventory(run_export_job(token, facility), facility)
        except Exception as e:  # noqa: BLE001 -- retry on anything; the last one is re-raised
            last_error = e
            print(f"WARN: {facility} attempt {attempt}/{FACILITY_RETRY_ATTEMPTS} failed: {e}",
                  file=sys.stderr)
            if attempt < FACILITY_RETRY_ATTEMPTS:
                time.sleep(FACILITY_RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"{facility}: giving up after {FACILITY_RETRY_ATTEMPTS} attempts -- {last_error}")


def main():
    supabase_url, supabase_key = supabase_config()
    token = get_uniware_token()

    rows = []
    for i, facility in enumerate(ALL_UNIWARE_FACILITIES, 1):
        print(f"[{i}/{len(ALL_UNIWARE_FACILITIES)}] {facility} ...", flush=True)
        # Deliberately NOT caught: one unreachable facility must abort the whole run rather than
        # publish a snapshot that's silently missing a store, which would understate its city's
        # total and could read as a stockout. The previous snapshot stays live until this succeeds.
        by_sku = fetch_facility_with_retries(token, facility)
        rows.extend({"facility": facility, "sku": sku, "on_hand": qty} for sku, qty in by_sku.items())

    replace_by_filter(supabase_url, supabase_key, "sop_uniware_inventory", rows,
                       {"facility": "not.is.null"})

    total = sum(r["on_hand"] for r in rows)
    print(f"Synced {len(rows)} facility x SKU row(s) across {len(ALL_UNIWARE_FACILITIES)} "
          f"facilities (total on-hand {total:.0f}).")


if __name__ == "__main__":
    main()
