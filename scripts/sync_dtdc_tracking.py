"""Polls DTDC's tracking API for every AWB logged in po_item_shipments
with courier='dtdc' (Bluedart's own AWBs are sync_bluedart_tracking.py's
job) and upserts the latest status into shipment_tracking.

Auth is a single static "x-access-token" header (customer code + secret,
no separate login/token-fetch step) -- confirmed working 2026-09-17
against a real AWB (110001047024, a delivered shipment booked by "UC
BOMBAY" under customer code GL12312).

Unlike Bluedart's API, DTDC's getTrackDetails takes exactly ONE AWB per
call (no batch/comma-separated form) -- this script loops one request per
AWB. Its trackDetails scan history is ordered oldest-first (Bluedart's is
newest-first), so "last scan" is the LAST array entry here.

Skips AWBs already Delivered in shipment_tracking from a prior run --
once delivered, status never changes again, so re-polling forever would
just waste API calls. (DTDC's other terminal states, e.g. RTO, aren't
confirmed yet -- add their exact strStatus text here once observed.)

Required environment variables:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  -- same as every other sync script
  DTDC_ACCESS_TOKEN                         -- the whole "x-access-token" header value,
                                                e.g. "GL12312_trk_json:<secret>"

Run with: python3 scripts/sync_dtdc_tracking.py
"""
import os
import sys
import time
from datetime import datetime, timezone

import requests

REQUEST_TIMEOUT = 30
DTDC_API = "https://blktracksvc.dtdc.com/dtdc-api/rest/JSONCnTrk/getTrackDetails"
TERMINAL_STATUSES = {"delivered"}  # trackHeader.strStatus, lowercased


def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing {name} environment variable.")
    return v


def supabase_config():
    return env("SUPABASE_URL").rstrip("/"), env("SUPABASE_SERVICE_ROLE_KEY")


def fetch_awbs_to_poll(supabase_url, key):
    """Distinct (awb_number, vendor_code) pairs from po_item_shipments
    whose courier is 'dtdc', minus AWBs already Delivered in
    shipment_tracking."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    r = requests.get(
        f"{supabase_url}/rest/v1/po_item_shipments",
        headers=headers,
        params={"select": "awb_number,vendor_code", "courier": "eq.dtdc"},
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Fetching po_item_shipments failed ({r.status_code}): {r.text[:500]}")
    shipments = r.json()

    r = requests.get(
        f"{supabase_url}/rest/v1/shipment_tracking",
        headers=headers,
        params={"select": "awb_number,status_type", "courier": "eq.dtdc"},
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


def parse_ddmmyyyy(date_str, time_str=None):
    """DTDC dates look like '05092026' (DDMMYYYY, no separators); times
    like '1508' (HHMM, 24hr). Returns an ISO date, or an ISO datetime if
    time_str is given -- None if unparseable/blank (DTDC leaves several
    date fields empty until that stage of the shipment happens)."""
    date_str = (date_str or "").strip()
    if len(date_str) != 8 or not date_str.isdigit():
        return None
    day, month, year = date_str[0:2], date_str[2:4], date_str[4:8]
    try:
        if time_str and len(time_str.strip()) == 4 and time_str.strip().isdigit():
            hour, minute = time_str[0:2], time_str[2:4]
            return datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=timezone.utc).isoformat()
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return None


def track_one(access_token, awb):
    body = {"trkType": "cnno", "strcnno": awb, "addtnlDtl": "Y"}
    try:
        r = requests.post(
            DTDC_API,
            headers={"Content-Type": "application/json", "x-access-token": access_token},
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        print(f"  DTDC request failed for {awb}: {type(e).__name__}")
        return None

    if not r.ok:
        print(f"  DTDC request failed for {awb} (HTTP {r.status_code})")
        return None

    data = r.json()
    if not data.get("statusFlag"):
        err = (data.get("errorDetails") or [{}])
        msg = next((e.get("value") for e in err if e.get("value")), data.get("status"))
        print(f"  DTDC could not track {awb}: {msg}")
        return None

    header = data.get("trackHeader") or {}
    details = data.get("trackDetails") or []
    latest_scan = details[-1] if details else None  # DTDC lists newest scan LAST

    status_text = header.get("strStatus")
    return {
        "awb_number": awb,
        "courier": "dtdc",
        "status_type": status_text,  # DTDC has no separate short code at header level -- store the status text itself
        "status_text": status_text,
        "origin": header.get("strOrigin"),
        "destination": header.get("strDestination"),
        "expected_delivery_date": parse_ddmmyyyy(header.get("strExpectedDeliveryDate")),
        "last_scan_text": latest_scan.get("strAction") if latest_scan else None,
        "last_scan_location": (latest_scan.get("strDestination") or latest_scan.get("strOrigin")) if latest_scan else None,
        "last_scan_at": parse_ddmmyyyy(latest_scan.get("strActionDate"), latest_scan.get("strActionTime")) if latest_scan else None,
        "raw": {
            "booked_date": header.get("strBookedDate"),
            "mode": header.get("strMode"),
            "pieces": header.get("strPieces"),
            "weight": header.get("strWeight"),
            "cust_code": header.get("strCNActCustCode"),
            "scans": [
                {
                    "action": d.get("strAction"), "code": d.get("strCode"),
                    "date": d.get("strActionDate"), "time": d.get("strActionTime"),
                    "origin": d.get("strOrigin"), "destination": d.get("strDestination"),
                }
                for d in details[-20:]
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
    access_token = env("DTDC_ACCESS_TOKEN")

    vendor_by_awb = fetch_awbs_to_poll(supabase_url, supabase_key)
    if not vendor_by_awb:
        print("No non-terminal DTDC AWBs to poll.")
        return

    all_rows = []
    for awb, vendor_code in sorted(vendor_by_awb.items()):
        row = track_one(access_token, awb)
        if row:
            row["vendor_code"] = vendor_code
            all_rows.append(row)
        time.sleep(0.5)  # be polite -- no documented rate limit

    upsert_rows(supabase_url, supabase_key, all_rows)
    print(f"Polled {len(vendor_by_awb)} AWB(s), updated {len(all_rows)} shipment_tracking row(s).")


if __name__ == "__main__":
    main()
