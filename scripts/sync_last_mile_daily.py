#!/usr/bin/env python3
"""
Last Mile Tracking, daily leg: pulls Sale Orders from Uniware, collapses
order-item rows to shipment (AWB) grain, and rebuilds last_mile_watchlist
in Supabase.

Ported from the awb-delivery-tracker project's awb_tracker/sale_orders.py
+ awb_tracker/watchlist.py, ADAPTED so its durable state lives in Postgres
instead of Claude's artifact database. That project had no persistent
disk (a scheduled cloud routine gets a fresh checkout every fire), so it
kept the watchlist in the artifact DB via a read-shard/run/write-shard
dance. This portal already has Postgres, so that workaround is gone --
this script just writes the table directly, exactly like every other
sync in scripts/.

WHY THIS PULLS VIA AN EXPORT JOB, NOT A LIVE SEARCH ENDPOINT
--------------------------------------------------------------
sync_to_supabase.py's PO/GRN sync calls live REST endpoints
(getPurchaseOrders, getPurchaseOrderDetails) -- but those live under
Uniware's purpose-built /services/rest/v1/purchase/purchaseOrder/... API.
No equivalent exists for Sale Orders. Confirmed against
awb-delivery-tracker/FINDINGS.md item 4 ("Sale Orders API" report --
still unresolved, rejected on an invalid column) and against
DATA_FETCHING_REFERENCE.md, which states the data model plainly: "an
export job produces a CSV; you run one per facility and merge." That is
not a workaround here, it is the only interface this resource has.

None of this requires a human to look at a downloaded file. The flow
below is identical in shape to every other automated sync in this repo:
create the export job, poll for the CSV, parse it in memory, write rows
to Postgres. Nobody ever opens the CSV.

THE SHIPMENT-GRAIN COLLAPSE
----------------------------
One AWB carries several Sale Order Items -- measured at 5.4x on real
Native volume (see FINDINGS.md section 8). Tracking at item grain would
multiply every courier call in the hourly job by five and make "how many
shipments are late" impossible to answer, so every item row sharing an
AWB collapses into one watchlist row here.

SCOPE
-----
Same four channels as the source project's watchlist.py, and the same
reasoning: CUSTOM_UC_APP holds the bulk (spares + refresh kits),
CUSTOM_UC_D2C_RO is purifiers only, CUSTOM_UC_MANUAL and
CUSTOM_UC_D2C_STORES are the same goods via other entry points. B2B and
internal BOM are out of scope.

CREDENTIALS
-----------
  UNIWARE_USERNAME, UNIWARE_PASSWORD  -- same account as every other sync
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Usage:
    python scripts/sync_last_mile_daily.py [--window-days N] [--dry-run]
"""
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

# Ported logic from the awb-delivery-tracker project -- see each module's
# docstring. These encode findings measured against real data (courier
# timezone drift, the courier-code rule table, return detection).
#
# This script used to hand-reimplement the collapse/cohort logic itself
# (a duplicate of watchlist.build_shipments()) and got FIVE real details
# wrong doing so: the adapter registry's import path, EXCLUDED_ADAPTERS'
# actual location, the tie-break in recency_key, needs_lsp_poll's cohort
# list, and a missing days_since_dispatch column entirely. Fixed 2026-09-18
# by deleting that duplicate and calling the real, tested functions
# directly instead -- see build_watchlist_rows() below. Do not reintroduce
# a parallel reimplementation of anything in last_mile_lib.watchlist.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from last_mile_lib import watchlist                    # noqa: E402
from last_mile_lib.dq import DQSink                     # noqa: E402
from last_mile_lib.sla import SlaRules                  # noqa: E402
from last_mile_lib.uniware_tz import to_ist             # noqa: E402

UNIWARE_BASE_URL = "https://urbanclap.unicommerce.com"
REQUEST_TIMEOUT = 90
AUTH_RETRY_ATTEMPTS = 3
AUTH_RETRY_BACKOFF_SECONDS = 5

DEFAULT_WINDOW_DAYS = 45  # matches awb_tracker's default -- wide enough to
                          # catch anything still open; only addedOn is
                          # filterable, so a rolling re-query + de-dupe on
                          # AWB (the upsert key) is how this stays correct.

# Requested export columns -- Uniware's internal names, order preserved in
# the CSV header. Verified accepted 2026-09-07 (see awb_tracker/sale_orders.py,
# ported verbatim).
COLUMNS = (
    "saleOrderCode", "soicode",
    "created", "updated", "displayOrderDateTime", "dispatchDate", "deliveryTime",
    "shippingAddressPincode", "shippingAddressCity",
    "skuCode", "itemTypeName", "skuName", "channel",
    "status", "SoiStatus", "cancellationReason",
    "cod",
    "shippingProvider", "shippingCourier", "shippingArrangedBy", "TrackingNumber",
    "ShippingPackageCode",
    "shippingTrackingStatus", "shippingCourierStatus", "shippingPackageStatusCode",
    "facility", "facilityCode",
    "returnedDate", "returnReason",
)

def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing {name} environment variable.")
    return v


def get_uniware_token():
    """Same retry discipline as sync_to_supabase.py's get_uniware_token() --
    a transient blip here must not fail the whole run."""
    username, password = env("UNIWARE_USERNAME"), env("UNIWARE_PASSWORD")
    last_error = None
    for attempt in range(1, AUTH_RETRY_ATTEMPTS + 1):
        try:
            resp = requests.get(
                f"{UNIWARE_BASE_URL}/oauth/token",
                params={"grant_type": "password", "client_id": "my-trusted-client",
                        "username": username, "password": password},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            last_error = f"Uniware auth request failed (network error): {type(e).__name__}"
            print(f"WARN: {last_error} (attempt {attempt}/{AUTH_RETRY_ATTEMPTS})", file=sys.stderr)
            if attempt < AUTH_RETRY_ATTEMPTS:
                time.sleep(AUTH_RETRY_BACKOFF_SECONDS * attempt)
            continue
        if not resp.ok:
            sys.exit(f"Uniware auth failed with HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        if "access_token" not in data:
            sys.exit(f"Uniware authentication failed: {data}")
        return data["access_token"]
    sys.exit(last_error)


def uniware_headers(token, facility_code):
    return {"Content-Type": "application/json", "Authorization": f"bearer {token}",
            "Facility": facility_code}


IST = timezone(timedelta(hours=5, minutes=30))


def list_enabled_facilities(session, token):
    """No Facility header on this one call -- verified against
    awb_tracker/uniware_client.py's list_enabled_facilities(), which calls
    self._headers() with no facility argument. Every OTHER call in this
    script needs the header; this is the one exception."""
    body = {"fromDate": "2000-01-01T00:00:00.000Z",
            "toDate": datetime.now(IST).strftime("%Y-%m-%dT00:00:00.000Z"),
            "dateType": "CREATED", "facilityStatus": "ALL"}
    r = session.post(f"{UNIWARE_BASE_URL}/services/rest/v1/facility/search",
                      headers={"Content-Type": "application/json", "Authorization": f"bearer {token}"},
                      json=body, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        sys.exit(f"facility/search failed ({r.status_code}): {r.text[:300]}")
    parties = (r.json() or {}).get("parties") or []
    return [p["facilityCode"] for p in parties
            if p.get("facilityStatus") == "ENABLED" and p.get("facilityCode")]


def added_on_filter(days):
    """`addedOn` is the only filter the Sale Orders report supports.
    Exact shape verified against awb_tracker/uniware_client.py's own
    added_on_filter() -- id/dateRange with epoch-millisecond bounds, NOT
    the name/type/value shape sync_to_supabase.py's PO search uses. The
    two export APIs take different filter shapes; do not assume they match."""
    end = int(time.time() * 1000)
    return [{"id": "addedOn", "dateRange": {"start": end - days * 86_400_000, "end": end}}]


def fetch_facility(session, token, facility, window_days, retries=3):
    """Create -> poll -> download -> parse. See awb_tracker/uniware_client.py's
    module docstring for the three traps this encodes:
      - the Facility header is mandatory (403 without it)
      - the body key is `exportColums` -- Uniware's own misspelling
      - poll until filePath is non-null, not merely until status says done
    """
    import csv
    import io

    body = {"exportJobTypeName": "Sale Orders", "exportColums": list(COLUMNS),
            "exportFilters": added_on_filter(window_days), "frequency": "ONETIME"}

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = session.post(f"{UNIWARE_BASE_URL}/services/rest/v1/export/job/create",
                              headers=uniware_headers(token, facility), json=body, timeout=REQUEST_TIMEOUT)
            j = r.json()
            job_code = j.get("jobCode")
            if not job_code:
                # HTTP 200 + successful:false is the normal failure shape here,
                # not an HTTP error -- see uniware_client.py's docstring.
                raise RuntimeError(f"export rejected: successful={j.get('successful')} errors={j.get('errors')}")

            deadline = time.time() + 300
            file_path = None
            while time.time() < deadline:
                r = session.post(f"{UNIWARE_BASE_URL}/services/rest/v1/export/job/status",
                                  headers=uniware_headers(token, facility),
                                  json={"jobCode": job_code}, timeout=REQUEST_TIMEOUT)
                sj = r.json() if r.ok else {}
                file_path = sj.get("filePath")
                if file_path:
                    break
                time.sleep(3.0)
            if not file_path:
                raise RuntimeError("job timed out waiting for filePath")

            d = session.get(file_path, timeout=(10, 180))
            if d.status_code != 200:
                raise RuntimeError(f"cloudfront download failed http={d.status_code}")

            reader = csv.DictReader(io.StringIO(d.text))
            return facility, list(reader), None
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print(f"  {facility} attempt {attempt}/{retries} failed: {last_err[:200]}", file=sys.stderr)
            if attempt < retries:
                time.sleep(4.0 * attempt)
    return facility, [], last_err


def to_row(ship):
    """Shipment -> a last_mile_watchlist row.

    Shipment keeps created_at/dispatch_date/delivery_time as Uniware's RAW
    strings (for round-trip fidelity), not calibrated datetimes -- so this
    is the one place that must independently run them through to_ist()
    before handing them to Postgres. Using the raw string directly would
    hand a `date`/`timestamptz` column a value in whatever format Uniware
    happened to export that row in (dd-mm-yyyy for some, yyyy-mm-dd for
    others -- see dates.py), and it would disagree with the cohort/
    days_since_dispatch this same script already computed via the
    calibrated value inside build_shipments().
    """
    created_dt = to_ist(ship.created_at, ship.adapter_id)
    dispatch_dt = to_ist(ship.dispatch_date, ship.adapter_id)
    delivery_dt = to_ist(ship.delivery_time, ship.adapter_id)
    return {
        "awb": ship.awb,
        "courier_code": ship.courier_code or None,
        "adapter_id": ship.adapter_id,
        "sale_order_codes": ship.sale_order_codes,
        "sale_order_item_codes": ship.sale_order_item_codes,
        "item_count": ship.item_count,
        "channel": ship.channel or None,
        "payment_type": ship.payment_type or None,
        "facility_code": ship.facility_code or None,
        "city": ship.city or None,
        "pincode": ship.pincode or None,
        "shipping_provider": ship.shipping_provider or None,
        "created_at_uniware": created_dt.isoformat() if created_dt else None,
        "dispatch_date": dispatch_dt.date().isoformat() if dispatch_dt else None,
        "delivery_time": delivery_dt.isoformat() if delivery_dt else None,
        "item_status": ship.item_status or None,
        "uniware_tracking_status": ship.uniware_tracking_status or None,
        "uniware_courier_status": ship.uniware_courier_status or None,
        "package_status_code": ship.package_status_code or None,
        "promised_date": ship.promised_date,
        "promise_days": ship.promise_days,
        "promise_source": ship.promise_source or None,
        "promise_slacode": ship.promise_slacode,
        "days_since_dispatch": ship.days_since_dispatch,
        "cohort": ship.cohort,
        "needs_lsp_poll": ship.needs_lsp_poll,
        "awb_pattern_ok": ship.awb_pattern_ok,
    }


def build_watchlist_rows(rows):
    """rows (raw Uniware CSV dicts) -> last_mile_watchlist row dicts.

    Thin wrapper around the real, tested pipeline in last_mile_lib.watchlist
    -- dedupe() then build_shipments(). No collapse/cohort logic lives here
    anymore; see the comment at this file's top for why.

    SlaRules() needs a rules CSV to exist or it raises FileNotFoundError --
    scripts/last_mile_lib/reference/serviceability_rules_active.csv ships
    as a header-only stub (SERVICEABILITYRULES_DP is Jarvis-sourced and
    VPN-gated, so this daily leg deliberately can't pull it), which makes
    every lookup miss and every promise fall through to ASSUMED -- exactly
    SlaRules' own documented fallback, not a workaround bolted on here.
    """
    deduped = watchlist.dedupe(rows)
    dq = DQSink(state_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dq"))
    ships, stats = watchlist.build_shipments(deduped, rules=SlaRules(), dq=dq)
    print(f"  intake stats: {stats}")
    dq_sum = dq.summary()
    if dq_sum:
        print(f"  data-quality items this pull: { {k: v.get('total_occurrences') for k, v in dq_sum.items()} }")
    return [to_row(s) for s in ships]


def supabase_config():
    return env("SUPABASE_URL").rstrip("/"), env("SUPABASE_SERVICE_ROLE_KEY")


def upsert_watchlist(supabase_url, key, rows):
    if not rows:
        return
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    for i in range(0, len(rows), 500):
        batch = rows[i:i + 500]
        r = requests.post(f"{supabase_url}/rest/v1/last_mile_watchlist",
                           headers=headers, params={"on_conflict": "awb"}, json=batch, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            sys.exit(f"Upsert into last_mile_watchlist failed ({r.status_code}): {r.text[:500]}")


def main():
    dry_run = "--dry-run" in sys.argv
    window_days = DEFAULT_WINDOW_DAYS
    if "--window-days" in sys.argv:
        window_days = int(sys.argv[sys.argv.index("--window-days") + 1])

    token = get_uniware_token()
    session = requests.Session()
    facilities = list_enabled_facilities(session, token)
    print(f"{len(facilities)} enabled facilities.")

    all_rows = []
    failures = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_facility, session, token, f, window_days): f for f in facilities}
        for fut in as_completed(futures):
            facility, rows, err = fut.result()
            if err:
                failures.append((facility, err))
            else:
                all_rows.extend(rows)
                print(f"  {facility}: {len(rows)} row(s)")

    if failures:
        # A partial merge that looks complete silently under-reports -- same
        # fail-fast discipline as sync_to_supabase.py's pull().
        for f, e in failures:
            print(f"FAILED {f}: {e}", file=sys.stderr)
        sys.exit(f"{len(failures)} facility export(s) failed -- aborting without writing a partial watchlist.")

    print(f"{len(all_rows)} order-item row(s) across {len(facilities)} facilities.")
    # NOT named `watchlist` -- that shadows the imported last_mile_lib.watchlist
    # module, which build_watchlist_rows() itself still needs to call.
    watchlist_rows = build_watchlist_rows(all_rows)
    print(f"Collapsed to {len(watchlist_rows)} shipment(s) in scope.")

    by_cohort = defaultdict(int)
    by_adapter = defaultdict(int)
    for w in watchlist_rows:
        by_cohort[w["cohort"]] += 1
        by_adapter[w["adapter_id"]] += 1
    print("  by cohort:", dict(by_cohort))
    print("  by adapter:", dict(by_adapter))

    if dry_run:
        print("[dry-run] nothing written.")
        return

    supabase_url, key = supabase_config()
    upsert_watchlist(supabase_url, key, watchlist_rows)
    print(f"Upserted {len(watchlist_rows)} row(s) into last_mile_watchlist.")


if __name__ == "__main__":
    main()
