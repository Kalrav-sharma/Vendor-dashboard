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
different tabs. Going through one table makes that impossible.

HOW WE READ INVENTORY: POST /services/rest/v1/inventory/inventorySnapshot/get
with {"itemTypeSKUs": [...]}, once per facility (facility is chosen by the
`Facility:` request header, not by the body), ~0.1s each -- the whole network is
under 3 seconds.

We originally did this via Uniware's async "Inventory Snapshot" EXPORT JOB
(create -> poll -> download a CSV), ported from
~/.claude/scripts/sync-warehouse-inventory-to-sheet.js, on the belief that no
synchronous endpoint existed. It does, and it returns the same numbers: checked
2026-09-16 across all 28 facilities x 6 SKUs, 165 of 168 cells identical and the
3 others off by exactly 1 unit -- real sales in the 20 minutes between the two
reads. The direct call is ~40x faster and, more importantly, doesn't depend on
Uniware's export queue, which was observed failing every job for a ~3-minute
stretch that same day. If this endpoint is ever withdrawn, the export-job
version is in git history.

ON-HAND is the response's `inventory` field. The call also returns
inventoryBlocked / badInventory / quarantinedInventory / openSale /
openPurchase / pendingStockTransfer / vendorInventory, all deliberately ignored
-- `inventory` alone is what the sheet's "Inventory" column held, so the portal
and the sheet keep meaning the same thing by "on hand".

Credentials, as GitHub Actions repo secrets (already provisioned for
refresh.yml -- no new secrets, but this workflow has to be granted them too):
  UNIWARE_USERNAME
  UNIWARE_PASSWORD
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""
import os
import sys
import time

import requests

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import (  # noqa: E402
    ALL_UNIWARE_FACILITIES, REQUEST_TIMEOUT, UNIWARE_SKU_MAP, replace_by_filter, supabase_config,
)

UNIWARE_BASE_URL = "https://urbanclap.unicommerce.com"
INVENTORY_PATH = "/services/rest/v1/inventory/inventorySnapshot/get"

AUTH_RETRY_ATTEMPTS = 3
AUTH_RETRY_BACKOFF_SECONDS = 5      # doubles each attempt: 5s, 10s
# The call is ~0.1s, so retrying is nearly free -- be patient rather than clever.
FACILITY_RETRY_ATTEMPTS = 4
FACILITY_RETRY_BACKOFF_SECONDS = 5  # linear: 5s, 10s, 15s


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


SKU_CODES = list(UNIWARE_SKU_MAP)


def fetch_facility_inventory(token, facility):
    """One facility's on-hand as {our SKU name: qty}.

    The facility comes from the `Facility:` header, not the body. A SKU missing from the response
    genuinely means zero stock of it there, so it stays 0; duplicate entries for one SKU are summed
    defensively (none observed)."""
    resp = requests.post(f"{UNIWARE_BASE_URL}{INVENTORY_PATH}",
                          headers=uniware_headers(token, facility),
                          json={"itemTypeSKUs": SKU_CODES}, timeout=REQUEST_TIMEOUT)
    if not resp.ok:
        raise RuntimeError(f"inventorySnapshot/get failed (HTTP {resp.status_code}): {resp.text[:300]}")
    payload = resp.json()
    # Uniware answers 200 with successful:false for application-level problems (an unknown facility
    # code, say), which would otherwise read as "this facility has no stock".
    if not payload.get("successful", False):
        raise RuntimeError(f"inventorySnapshot/get reported failure: {str(payload)[:300]}")

    by_sku = {sku: 0.0 for sku in UNIWARE_SKU_MAP.values()}
    matched = 0
    for snap in payload.get("inventorySnapshots") or []:
        sku = UNIWARE_SKU_MAP.get(snap.get("itemTypeSKU"))  # exact match -- codes differ in casing
        if not sku:
            continue
        by_sku[sku] += float(snap.get("inventory") or 0)
        matched += 1
    if matched == 0:
        print(f"WARNING: {facility} returned no rows for any of our 6 SKUs -- treating the whole "
              f"facility as zero stock.", file=sys.stderr)
    return by_sku


def fetch_facility_with_retries(token, facility):
    last_error = None
    for attempt in range(1, FACILITY_RETRY_ATTEMPTS + 1):
        try:
            return fetch_facility_inventory(token, facility)
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
