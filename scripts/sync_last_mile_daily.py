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
# timezone drift, the courier-code rule table, return detection); they are
# deliberately copied rather than re-derived.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from last_mile_lib import registry                          # noqa: E402
from last_mile_lib.dates import IST, now_ist, parse_dt      # noqa: E402
from last_mile_lib.uniware_status import is_return          # noqa: E402
from last_mile_lib.uniware_tz import to_ist                 # noqa: E402

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

# The four channels that hold spares, refresh kits and RO purifiers.
# B2B and internal BOM are excluded. See awb_tracker/watchlist.py.
IN_SCOPE_CHANNELS = frozenset({
    "CUSTOM_UC_APP", "CUSTOM_UC_D2C_RO", "CUSTOM_UC_MANUAL", "CUSTOM_UC_D2C_STORES",
})

# Order-item statuses meaning the shipment is settled and needs no more polling.
CLOSED_ITEM_STATUS = frozenset({"DELIVERED", "CANCELLED"})

# Cohort horizons, copied from awb_tracker/watchlist.py -- not arbitrary.
LIVE_DISPATCH_HORIZON_DAYS = 15   # dispatched within this = actively tracked
BACKLOG_HORIZON_DAYS = 60         # beyond the horizon but still not delivered

BLANKS = {"", "-", "NA", "N/A", "0", "NULL", "NONE"}


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


def _g(row, key):
    return (row.get(key) or "").strip()


def clean_awb(value):
    awb = (value or "").strip().upper()
    return "" if awb in BLANKS else awb


def recency_key(row):
    """Latest-wins ordering for de-duplication.

    Preserves the upstream tie-break: a DELIVERED row beats an undelivered
    one, then later `Updated`. Getting this wrong silently lost ~6,800 rows
    upstream, so it is reproduced rather than re-invented.
    """
    delivered = 1 if _g(row, "Sale Order Item Status") == "DELIVERED" else 0
    updated = parse_dt(_g(row, "Updated")) or datetime.min.replace(tzinfo=IST)
    return (delivered, updated)


def in_scope(row):
    return _g(row, "Channel Name") in IN_SCOPE_CHANNELS


def build_watchlist(rows):
    """Collapse order-item rows sharing one AWB into one shipment record.

    Ported from awb_tracker/watchlist.py. The pieces below are load-bearing
    and were each got WRONG in this script's first draft -- do not
    "simplify" any of them back:

      lead item      -- the most ADVANCED item drives the shipment's state
                        (max by recency_key), not an arbitrary first row.
      adapter        -- registry.resolve() against courier_map.json, which
                        keys on `Shipping Courier` and does exact->regex->
                        longest-prefix. Not string matching on the provider
                        field, which collapses DTDC's 15 variants.
      dispatch date  -- normalised through to_ist() for that adapter BEFORE
                        any day-count. Shadowfax's timestamps are stored in
                        UTC while everyone else's are IST; skipping this
                        manufactures a phantom 5h30m gap on the majority of
                        volume.
      returns        -- is_return() closes them at intake. A return keeps
                        item_status DISPATCHED, so CLOSED_ITEM_STATUS alone
                        never catches one, and the rolling 45-day export
                        would re-add every return every morning forever.
      excluded       -- in-house fleet / Porter / unmapped are cohorted out
                        at intake, not filtered at each surface.

    Not ported: the SLA-rules promise lookup. That needs
    SERVICEABILITYRULES_DP from Jarvis, which is VPN-gated; this daily leg
    is deliberately Uniware-only so it can run on GitHub's hosted runners.
    promised_date / promise_source stay null until that is wired in.
    """
    by_awb = defaultdict(list)
    skipped_no_awb = 0
    for row in rows:
        if not in_scope(row):
            continue
        awb = clean_awb(_g(row, "Tracking Number"))
        if not awb:
            skipped_no_awb += 1
            continue
        by_awb[awb].append(row)

    now = now_ist()
    watchlist = []
    unmapped_couriers = defaultdict(int)

    for awb, items in by_awb.items():
        lead = max(items, key=recency_key)
        courier_code = _g(lead, "Shipping Courier")
        res = registry.resolve(courier_code)
        if res.is_unknown:
            unmapped_couriers[courier_code or "(blank)"] += 1

        item_status = _g(lead, "Sale Order Item Status")
        track_status = _g(lead, "Shipping Tracking Status")
        pkg_code = _g(lead, "Shipping Package Status Code")

        dispatch_dt = to_ist(_g(lead, "Dispatch Date"), res.adapter_id)
        days_since = (now.date() - dispatch_dt.date()).days if dispatch_dt else None

        if res.adapter_id in registry.EXCLUDED_ADAPTERS:
            cohort = "excluded"
        elif item_status in CLOSED_ITEM_STATUS:
            cohort = "closed"
        elif is_return(track_status, pkg_code):
            cohort = "closed"
        elif days_since is None:
            cohort = "no_dispatch_date"
        elif days_since <= LIVE_DISPATCH_HORIZON_DAYS:
            cohort = "live"
        elif days_since <= BACKLOG_HORIZON_DAYS:
            cohort = "backlog"
        else:
            cohort = "aged_out"

        created_dt = to_ist(_g(lead, "Created"), res.adapter_id)
        delivery_dt = to_ist(_g(lead, "Delivery Time"), res.adapter_id)

        watchlist.append({
            "awb": awb,
            "courier_code": courier_code or None,
            "adapter_id": res.adapter_id,
            "sale_order_codes": sorted({_g(r, "Sale Order Code") for r in items} - {""}),
            "sale_order_item_codes": sorted({_g(r, "Sale Order Item Code") for r in items} - {""}),
            "item_count": len(items),
            "channel": _g(lead, "Channel Name") or None,
            "payment_type": "COD" if _g(lead, "COD") in ("1", "true", "True") else "Prepaid",
            "facility_code": _g(lead, "Facility Code") or None,
            "city": _g(lead, "Shipping Address City") or None,
            "pincode": _g(lead, "Shipping Address Pincode") or None,
            "shipping_provider": _g(lead, "Shipping provider") or None,
            "created_at_uniware": created_dt.isoformat() if created_dt else None,
            "dispatch_date": dispatch_dt.date().isoformat() if dispatch_dt else None,
            "delivery_time": delivery_dt.isoformat() if delivery_dt else None,
            "item_status": item_status or None,
            "uniware_tracking_status": track_status or None,
            "uniware_courier_status": _g(lead, "Shipping Courier Status") or None,
            "package_status_code": pkg_code or None,
            "cohort": cohort,
            # Only cohorts that are still in flight get polled. 'excluded' and
            # 'aged_out' are deliberately not polled but ARE kept on file so
            # the coverage funnel reconciles against its own input.
            "needs_lsp_poll": cohort in ("live", "backlog"),
        })

    if skipped_no_awb:
        print(f"  {skipped_no_awb} in-scope row(s) had no AWB (data-quality item).")
    if unmapped_couriers:
        top = sorted(unmapped_couriers.items(), key=lambda kv: -kv[1])[:5]
        print(f"  unmapped couriers: {dict(top)}")
    return watchlist


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
    watchlist = build_watchlist(all_rows)
    print(f"Collapsed to {len(watchlist)} shipment(s) in scope.")

    by_cohort = defaultdict(int)
    by_adapter = defaultdict(int)
    for w in watchlist:
        by_cohort[w["cohort"]] += 1
        by_adapter[w["adapter_id"]] += 1
    print("  by cohort:", dict(by_cohort))
    print("  by adapter:", dict(by_adapter))

    if dry_run:
        print("[dry-run] nothing written.")
        return

    supabase_url, key = supabase_config()
    upsert_watchlist(supabase_url, key, watchlist)
    print(f"Upserted {len(watchlist)} row(s) into last_mile_watchlist.")


if __name__ == "__main__":
    main()
