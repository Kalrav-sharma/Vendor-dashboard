"""Polls Softpal's tracking API (Lets Transport's tracking is white-labelled
through Softpal Technologies) for every AWB logged in po_item_shipments
with courier='letstransport' and upserts the latest status into
shipment_tracking.

Confirmed working 2026-09-22 against a real, live shipment (100032652,
booked by Lexcru Water Tech, in transit AHMEDABAD -> BANGALORE).

Despite Softpal_Tracking_API_Document_v2.pdf documenting "Response Format:
XML", the live API actually returns plain JSON -- built against the real
observed response, not the doc's claim.

Auth is unusual: no API key/token at all, just ShipmentNo + a fixed HostId
(162, Urban Company's own account with Softpal) as query params on a GET.

Like DTDC's API (and unlike Bluedart's), this takes exactly ONE shipment
number per call (no batch/comma-separated form) -- this script loops one
request per AWB.

Sheet_History is ordered newest-first in the one real response observed,
matching Bluedart's convention -- but this sorts explicitly by status_date
rather than trust that ordering blindly.

Skips AWBs already Delivered in shipment_tracking from a prior run --
once delivered, status never changes again, so re-polling forever would
just waste API calls. (No other terminal states, e.g. RTO, are confirmed
yet -- add their exact current_status_name text here once observed.)

Required environment variables:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  -- same as every other sync script
  LETS_TRANSPORT_HOST_ID                    -- Softpal's HostId query param, e.g. "162"

Run with: python3 scripts/sync_lets_transport_tracking.py
"""
import os
import sys
import time
from datetime import datetime, timezone

import requests

REQUEST_TIMEOUT = 30
SOFTPAL_API = "https://eztrackwebapi159.softpal.in/V1/TrackingApiCommon_Softpal"
TERMINAL_STATUSES = {"delivered"}  # current_status_name, lowercased


def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing {name} environment variable.")
    return v


def supabase_config():
    return env("SUPABASE_URL").rstrip("/"), env("SUPABASE_SERVICE_ROLE_KEY")


def fetch_awbs_to_poll(supabase_url, key):
    """Distinct (awb_number, vendor_code) pairs from po_item_shipments
    whose courier is 'letstransport', minus AWBs already Delivered in
    shipment_tracking."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    r = requests.get(
        f"{supabase_url}/rest/v1/po_item_shipments",
        headers=headers,
        params={"select": "awb_number,vendor_code", "courier": "eq.letstransport"},
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Fetching po_item_shipments failed ({r.status_code}): {r.text[:500]}")
    shipments = r.json()

    r = requests.get(
        f"{supabase_url}/rest/v1/shipment_tracking",
        headers=headers,
        params={"select": "awb_number,status_type", "courier": "eq.letstransport"},
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Fetching shipment_tracking failed ({r.status_code}): {r.text[:500]}")
    terminal_awbs = {
        row["awb_number"] for row in r.json()
        if (row.get("status_type") or "").strip().lower() in TERMINAL_STATUSES
    }

    by_awb = {}
    for s in shipments:
        awb = (s.get("awb_number") or "").strip()
        if awb and awb not in terminal_awbs:
            by_awb[awb] = s.get("vendor_code")
    return by_awb


def strip_country(place):
    """"AHMEDABAD-INDIA" -> "AHMEDABAD" -- Softpal always suffixes
    origin/dest with "-INDIA"; every other courier here shows plain city
    names, so this keeps the Route column consistent."""
    if not place:
        return place
    return place[:-6] if place.upper().endswith("-INDIA") else place


def track_one(host_id, shipment_no):
    try:
        r = requests.get(
            SOFTPAL_API,
            params={"ShipmentNo": shipment_no, "HostId": host_id},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        print(f"  Softpal request failed for {shipment_no}: {type(e).__name__}")
        return None

    if not r.ok:
        print(f"  Softpal request failed for {shipment_no} (HTTP {r.status_code})")
        return None

    data = r.json()
    if not data.get("is_sucessful") or data.get("result") != 1:
        print(f"  Softpal could not track {shipment_no}: {data.get('error_message')}")
        return None

    header = data.get("ConsignmentDetails_Traking") or {}
    history = data.get("Sheet_History") or []
    # Sort defensively rather than trust the API's own ordering.
    history_sorted = sorted(history, key=lambda h: h.get("status_date") or "", reverse=True)
    latest_scan = history_sorted[0] if history_sorted else None

    status_name = header.get("current_status_name")
    return {
        "awb_number": str(shipment_no),
        "courier": "letstransport",
        "status_type": status_name,  # no reliable short-code vocabulary -- see format.js's LETS_TRANSPORT_STATUS_META
        "status_text": status_name,
        "origin": strip_country(header.get("origin_name")),
        "destination": strip_country(header.get("dest_name")),
        "expected_delivery_date": header.get("ExpectedDeliveryDate"),  # already ISO, or None -- rarely populated
        "last_scan_text": latest_scan.get("status") if latest_scan else None,
        "last_scan_location": latest_scan.get("dispatch_location_name") if latest_scan else None,
        "last_scan_at": latest_scan.get("status_date") if latest_scan else None,  # already ISO 8601
        "raw": {
            "status_code": header.get("status_code"),
            "carrier_name": header.get("carrier_name"),
            "service_name": header.get("service_name"),
            "no_of_pieces": header.get("no_of_pieces"),
            "actual_weight": header.get("actual_weight"),
            "date_of_booking": header.get("date_of_booking"),
            "history": [
                {"status": h.get("status"), "status_date": h.get("status_date"),
                 "dispatch_location_name": h.get("dispatch_location_name"), "destination": h.get("destination")}
                for h in history_sorted[:20]
            ],
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_rows(supabase_url, key, rows):
    if not rows:
        return
    headers = {
        "apikey": key, "Authorization": f"Bearer {key}",
        "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    r = requests.post(
        f"{supabase_url}/rest/v1/shipment_tracking",
        headers=headers,
        params={"on_conflict": "courier,awb_number"},
        json=rows,
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Upsert into shipment_tracking failed ({r.status_code}): {r.text[:500]}")


def main():
    supabase_url, supabase_key = supabase_config()
    host_id = env("LETS_TRANSPORT_HOST_ID")

    vendor_by_awb = fetch_awbs_to_poll(supabase_url, supabase_key)
    if not vendor_by_awb:
        print("No non-terminal Lets Transport AWBs to poll.")
        return

    all_rows = []
    for awb, vendor_code in sorted(vendor_by_awb.items()):
        row = track_one(host_id, awb)
        if row:
            row["vendor_code"] = vendor_code
            all_rows.append(row)
        time.sleep(0.5)  # be polite -- no documented rate limit

    upsert_rows(supabase_url, supabase_key, all_rows)
    print(f"Polled {len(vendor_by_awb)} AWB(s), updated {len(all_rows)} shipment_tracking row(s).")


if __name__ == "__main__":
    main()
