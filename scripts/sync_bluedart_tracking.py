"""Polls Bluedart's legacy Track & Trace API for every AWB logged in
po_item_shipments with courier='bluedart' (DTDC's own AWBs are
scripts/sync_dtdc_tracking.py's job) and upserts the latest status into
shipment_tracking.

Auth is LoginID + LicenceKey (query params on every call, no separate
token/login step) -- confirmed working 2026-09-13 against a real AWB.
Endpoint/field shapes were confirmed against a real open-source Bluedart
integration (github.com/sagarv1997/bluedart-tracking-json-api-nodejs-cf)
since Bluedart gave no documentation with these credentials.

Skips AWBs already in a terminal state (Delivered/RTO) in shipment_tracking
from a prior run -- once a shipment is done, its status never changes
again, so re-polling it forever would just waste API calls.

Required environment variables:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  -- same as every other sync script
  BLUEDART_LOGIN_ID, BLUEDART_LICENCE_KEY  -- Bluedart Track & Trace API creds

Run with: python3 scripts/sync_bluedart_tracking.py
"""
import os
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

REQUEST_TIMEOUT = 30
BLUEDART_API = "https://api.bluedart.com/servlet/RoutingServlet"
CHUNK_SIZE = 20  # AWBs per Bluedart call -- not documented, kept conservative
TERMINAL_STATUS_TYPES = {"DL", "RT"}  # Delivered, RTO -- never change again


def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing {name} environment variable.")
    return v


def supabase_config():
    return env("SUPABASE_URL").rstrip("/"), env("SUPABASE_SERVICE_ROLE_KEY")


def fetch_awbs_to_poll(supabase_url, key):
    """Distinct (awb_number, vendor_code) pairs from po_item_shipments
    whose courier is 'bluedart', minus AWBs already terminal in
    shipment_tracking -- DTDC's own AWBs (scripts/sync_dtdc_tracking.py's
    job) are filtered out here, not just left to fetch_awbs_to_poll's
    caller, so this script never wastes a Bluedart API call on one."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    r = requests.get(
        f"{supabase_url}/rest/v1/po_item_shipments",
        headers=headers,
        params={"select": "awb_number,vendor_code", "courier": "eq.bluedart"},
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Fetching po_item_shipments failed ({r.status_code}): {r.text[:500]}")
    shipments = r.json()

    r = requests.get(
        f"{supabase_url}/rest/v1/shipment_tracking",
        headers=headers,
        params={
            "select": "awb_number,status_type", "courier": "eq.bluedart",
            "status_type": f"in.({','.join(TERMINAL_STATUS_TYPES)})",
        },
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Fetching shipment_tracking failed ({r.status_code}): {r.text[:500]}")
    terminal_awbs = {row["awb_number"] for row in r.json()}

    by_awb = {}
    for s in shipments:
        awb = (s.get("awb_number") or "").strip()
        if awb and awb not in terminal_awbs:
            by_awb[awb] = s.get("vendor_code")
    return by_awb


def chunked(items, size):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def parse_date(text):
    """Bluedart dates look like '12 September 2026'."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def text_of(el, tag):
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else None


def parse_shipment(shipment_el):
    awb = shipment_el.get("WaybillNo")
    if not awb:
        return None

    scans = []
    for scan_el in shipment_el.findall("./Scans/ScanDetail"):
        scans.append({
            "scan": text_of(scan_el, "Scan"),
            "scan_type": text_of(scan_el, "ScanType"),
            "date": text_of(scan_el, "ScanDate"),
            "time": text_of(scan_el, "ScanTime"),
            "location": text_of(scan_el, "ScannedLocation"),
        })
    latest_scan = scans[0] if scans else None  # Bluedart lists newest scan first

    last_scan_at = None
    if latest_scan and latest_scan["date"] and latest_scan["time"]:
        try:
            last_scan_at = datetime.strptime(
                f"{latest_scan['date']} {latest_scan['time']}", "%d-%b-%Y %H:%M"
            ).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass

    return {
        "awb_number": awb,
        "courier": "bluedart",
        "status_type": text_of(shipment_el, "StatusType"),
        "status_text": text_of(shipment_el, "Status"),
        "origin": text_of(shipment_el, "Origin"),
        "destination": text_of(shipment_el, "Destination"),
        "expected_delivery_date": parse_date(text_of(shipment_el, "ExpectedDeliveryDate")),
        "last_scan_text": latest_scan["scan"] if latest_scan else None,
        "last_scan_location": latest_scan["location"] if latest_scan else None,
        "last_scan_at": last_scan_at,
        "raw": {
            "prod_code": text_of(shipment_el, "Prodcode"),
            "service": text_of(shipment_el, "Service"),
            "pickup_date": text_of(shipment_el, "PickUpDate"),
            "sender_name": text_of(shipment_el, "SenderName"),
            "consignee": text_of(shipment_el, "Consignee") or text_of(shipment_el, "ToAttention"),
            "weight": text_of(shipment_el, "Weight"),
            "scans": scans[:20],
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def track_batch(login_id, licence_key, awbs):
    params = {
        "handler": "tnt",
        "action": "custawbquery",
        "loginid": login_id,
        "awb": "awb",
        "numbers": ",".join(awbs),
        "format": "xml",
        "lickey": licence_key,
        "verno": "1.3",
        "scan": "1",
    }
    url = f"{BLUEDART_API}?{urllib.parse.urlencode(params)}"
    r = requests.get(url, headers={"User-Agent": "lexcru-po-tracker/1.0"}, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        print(f"  Bluedart request failed ({r.status_code}) for batch of {len(awbs)} AWB(s) -- skipping.")
        return []

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        print(f"  Could not parse Bluedart response for batch of {len(awbs)} AWB(s): {e}")
        return []

    results = []
    for shipment_el in root.findall(".//Shipment"):
        parsed = parse_shipment(shipment_el)
        if parsed:
            results.append(parsed)
    return results


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
    login_id = env("BLUEDART_LOGIN_ID")
    licence_key = env("BLUEDART_LICENCE_KEY")

    vendor_by_awb = fetch_awbs_to_poll(supabase_url, supabase_key)
    if not vendor_by_awb:
        print("No non-terminal AWBs to poll.")
        return

    all_rows = []
    for batch in chunked(sorted(vendor_by_awb), CHUNK_SIZE):
        parsed = track_batch(login_id, licence_key, batch)
        for row in parsed:
            row["vendor_code"] = vendor_by_awb.get(row["awb_number"])
        all_rows.extend(parsed)
        time.sleep(0.5)  # be polite to a legacy API with no documented rate limit

    upsert_rows(supabase_url, supabase_key, all_rows)
    print(f"Polled {len(vendor_by_awb)} AWB(s), updated {len(all_rows)} shipment_tracking row(s).")


if __name__ == "__main__":
    main()
