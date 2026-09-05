#!/usr/bin/env python3
"""
Pulls Lexcru Water Tech Pvt Ltd purchase orders + GRNs from Uniware across
the 5 Native facilities (created Aug 1 2026 onward), and upserts them into
Supabase Postgres tables (purchase_orders, grns, and their SKU-level line
items po_items / grn_items — Uniware returns these in the same detail
responses already being fetched, so this costs no extra API calls). The
vendor/admin portal pages under docs/ read directly from Supabase (via
supabase-js + Row Level Security) — this script's only job is keeping that
database current.

Designed to run unattended on a schedule (GitHub Actions cron, every 5 min)
with zero human/LLM involvement per run. Credentials come from environment
variables, set as GitHub Actions repo secrets, never committed:
  UNIWARE_USERNAME, UNIWARE_PASSWORD  — read-only Uniware account
  SUPABASE_URL                        — e.g. https://xxxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY           — bypasses RLS; server-side only,
                                         NEVER exposed to the docs/ frontend

Cost-control design (this runs every 5 minutes, forever):
  - Supabase itself is the persisted state (replacing the old local
    docs/data.json). On each run we query it for this vendor's existing
    PO codes + statuses, then:
      1. Re-fetch full detail ONLY for POs whose last known status is not
         terminal (COMPLETE/REJECTED/CANCELLED/CLOSED) — open POs can still
         change (partial receipts, approval, rejection).
      2. Search only a short recent window (RECENT_WINDOW_DAYS) per facility
         for newly created PO codes, rather than re-listing the entire
         Aug-1-to-today range every run.
      3. Terminal POs already in Supabase are left untouched — no re-fetch,
         no re-write.
  - GRNs are looked up only for POs that got a fresh detail fetch this run
    and have inflowReceiptsCount > 0 — same discipline as PO detail.
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
HEARTBEAT_PATH = os.path.join(REPO_DIR, "docs", ".last_sync")

IST = timezone(timedelta(hours=5, minutes=30))
UNIWARE_BASE_URL = "https://urbanclap.unicommerce.com"
REQUEST_TIMEOUT = 30

FACILITIES = ["PB-UC-GGN", "PB-UC-KOL", "PB-UC-BLR", "PB-UC-BOMBAY", "PB-UC-HYD"]
VENDOR_CODE = "Vendor-156"
VENDOR_NAME = "LEXCRU WATER TECH PVT LTD"  # Uniware masks vendorName in its API responses,
                                            # so this is the only source of truth for it

START_DATE = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
RECENT_WINDOW_DAYS = 4  # how far back to re-search for newly created PO codes each run
TERMINAL_STATUSES = {"COMPLETE", "REJECTED", "CANCELLED", "CLOSED"}


# ---------------------------------------------------------------------
# Uniware auth + fetch (unchanged from the original fetch_and_render.py)
# ---------------------------------------------------------------------

def get_uniware_token():
    username = os.environ.get("UNIWARE_USERNAME")
    password = os.environ.get("UNIWARE_PASSWORD")
    if not username or not password:
        sys.exit("Missing UNIWARE_USERNAME / UNIWARE_PASSWORD environment variables.")
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
        sys.exit(f"Uniware auth request failed (network error): {type(e).__name__}")
    if not resp.ok:
        sys.exit(f"Uniware auth failed with HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if "access_token" not in data:
        sys.exit(f"Uniware authentication failed: {data}")
    return data["access_token"]


def uniware_headers(token, facility_code):
    return {
        "Content-Type": "application/json",
        "Authorization": f"bearer {token}",
        "Facility": facility_code,
    }


def search_po_codes(session, token, facility, start_dt, end_dt):
    body = {
        "createdBetween": {
            "start": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    }
    try:
        r = session.post(
            f"{UNIWARE_BASE_URL}/services/rest/v1/purchase/purchaseOrder/getPurchaseOrders",
            headers=uniware_headers(token, facility),
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        return set(r.json().get("purchaseOrderCodes") or [])
    except Exception as e:
        print(f"WARN: PO search failed for {facility}: {e}", file=sys.stderr)
        return set()


def fetch_po_detail(session, token, facility, code):
    try:
        r = session.post(
            f"{UNIWARE_BASE_URL}/services/rest/v1/purchase/purchaseOrder/getPurchaseOrderDetails",
            headers=uniware_headers(token, facility),
            json={"purchaseOrderCode": code},
            timeout=REQUEST_TIMEOUT,
        )
        d = r.json()
        return d.get("purchaseOrder", d)
    except Exception as e:
        print(f"WARN: detail fetch failed for {facility}/{code}: {e}", file=sys.stderr)
        return None


def fetch_grn_codes_for_po(session, token, facility, po_code):
    try:
        r = session.post(
            f"{UNIWARE_BASE_URL}/services/rest/v1/purchase/inflowReceipt/getInflowReceipts",
            headers=uniware_headers(token, facility),
            json={"purchaseOrderCode": po_code},
            timeout=REQUEST_TIMEOUT,
        )
        return r.json().get("inflowReceiptCodes") or []
    except Exception as e:
        print(f"WARN: GRN code search failed for {facility}/{po_code}: {e}", file=sys.stderr)
        return []


def fetch_grn_detail(session, token, facility, grn_code):
    """Returns (header_dict, item_rows) — item_rows is [] on any failure."""
    try:
        r = session.post(
            f"{UNIWARE_BASE_URL}/services/rest/v1/purchase/inflowReceipt/getInflowReceipt",
            headers=uniware_headers(token, facility),
            json={"inflowReceiptCode": grn_code},
            timeout=REQUEST_TIMEOUT,
        )
        ir = r.json().get("inflowReceipt", r.json())
    except Exception as e:
        print(f"WARN: GRN detail fetch failed for {facility}/{grn_code}: {e}", file=sys.stderr)
        return None, []

    created_ms = ir.get("created")
    created_iso = None
    if created_ms:
        try:
            created_iso = datetime.fromtimestamp(int(created_ms) / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            created_iso = None

    header = {
        "grn_code": ir.get("code"),
        "status": ir.get("statusCode"),
        "created_at": created_iso,
        "vendor_invoice_number": ir.get("vendorInvoiceNumber"),
        "vendor_invoice_date": ir.get("vendorInvoiceDate"),
        "total_received_amount": ir.get("totalReceivedAmount"),
        "total_rejected_amount": ir.get("totalRejectedAmount"),
    }

    item_rows = [
        {
            "item_sku": it.get("itemSKU"),
            "item_name": it.get("itemTypeName"),
            "quantity": it.get("quantity"),
            "rejected_quantity": it.get("rejectedQuantity"),
            "unit_price": it.get("unitPrice"),
        }
        for it in (ir.get("inflowReceiptItems") or [])
    ]
    return header, item_rows


def fetch_grns_for_po(session, token, facility, po_code):
    grn_rows = []
    grn_item_rows = []
    for grn_code in fetch_grn_codes_for_po(session, token, facility, po_code):
        header, items = fetch_grn_detail(session, token, facility, grn_code)
        if not header:
            continue
        header["po_code"] = po_code
        header["vendor_code"] = VENDOR_CODE
        grn_rows.append(header)
        for it in items:
            it["grn_code"] = header["grn_code"]
            it["po_code"] = po_code
            it["vendor_code"] = VENDOR_CODE
            grn_item_rows.append(it)
    return grn_rows, grn_item_rows


def build_po_row(facility, code, po):
    """Returns (po_row, inflow_receipts_count, item_rows)."""
    items = po.get("purchaseOrderItems") or []
    created_ms = po.get("created") or po.get("createdDate")
    created_iso = None
    if created_ms:
        try:
            created_iso = datetime.fromtimestamp(int(created_ms) / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            created_iso = None

    vendor_code = po.get("vendorCode")
    po_row = {
        "po_code": code,
        "facility": facility,
        "vendor_code": vendor_code,
        "vendor_name": VENDOR_NAME,  # Uniware masks vendorName in the API; VENDOR_NAME is the known mapping
        "status": po.get("statusCode"),
        "created_at": created_iso,
        "total_amount": (po.get("purchaseOrderPriceSummary") or {}).get("totalAmount"),
        "qty_ordered": sum(it.get("quantity", 0) or 0 for it in items),
        "qty_received": sum(it.get("receivedQuantity", 0) or 0 for it in items),
        "qty_pending": sum(it.get("pendingQuantity", 0) or 0 for it in items),
        "qty_rejected": sum(it.get("rejectedQuantity", 0) or 0 for it in items),
        "num_items": len(items),
    }

    item_rows = [
        {
            "po_code": code,
            "vendor_code": vendor_code,
            "item_sku": it.get("itemSKU"),
            "item_name": it.get("itemTypeName"),
            "quantity": it.get("quantity"),
            "received_quantity": it.get("receivedQuantity"),
            "pending_quantity": it.get("pendingQuantity"),
            "rejected_quantity": it.get("rejectedQuantity"),
            "unit_price": it.get("unitPrice"),
            "max_retail_price": it.get("maxRetailPrice"),
            "subtotal": it.get("subtotal"),
            "total": it.get("total"),
        }
        for it in items
    ]

    return po_row, po.get("inflowReceiptsCount") or 0, item_rows


# ---------------------------------------------------------------------
# Supabase (service_role — bypasses RLS, server-side only)
# ---------------------------------------------------------------------

def supabase_config():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY environment variables.")
    return url.rstrip("/"), key


def supabase_headers(key):
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def fetch_existing_po_state(session, supabase_url, key, vendor_code):
    """Returns {po_code: {"facility": ..., "status": ...}} for this vendor."""
    r = session.get(
        f"{supabase_url}/rest/v1/purchase_orders",
        headers=supabase_headers(key),
        params={"vendor_code": f"eq.{vendor_code}", "select": "po_code,facility,status"},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return {row["po_code"]: row for row in r.json()}


def upsert_rows(session, supabase_url, key, table, on_conflict, rows):
    if not rows:
        return
    r = session.post(
        f"{supabase_url}/rest/v1/{table}",
        headers={**supabase_headers(key), "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": on_conflict},
        json=rows,
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        print(f"WARN: upsert into {table} failed ({r.status_code}): {r.text[:500]}", file=sys.stderr)


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    supabase_url, supabase_key = supabase_config()
    token = get_uniware_token()
    session = requests.Session()

    existing = fetch_existing_po_state(session, supabase_url, supabase_key, VENDOR_CODE)
    now_utc = datetime.now(timezone.utc)
    is_first_run = not existing
    search_start = START_DATE if is_first_run else now_utc - timedelta(days=RECENT_WINDOW_DAYS)

    # 1. Candidate PO codes: known-open ones already in Supabase, plus
    #    anything created in the search window (first run backfills the
    #    full START_DATE..now range, chunked to dodge Uniware's wide-range
    #    gotcha; every later run only re-searches the short recent window).
    codes_to_refresh = {}  # code -> facility
    for facility in FACILITIES:
        cursor = search_start
        found = set()
        while cursor < now_utc:
            chunk_end = min(cursor + timedelta(days=14), now_utc)
            found |= search_po_codes(session, token, facility, cursor, chunk_end)
            cursor = chunk_end
        for code in found:
            codes_to_refresh[code] = facility

    for code, row in existing.items():
        if row.get("status") not in TERMINAL_STATUSES:
            codes_to_refresh.setdefault(code, row["facility"])

    # 2. Fetch fresh PO detail for all candidates, in parallel.
    po_rows = []
    po_item_rows = []
    inflow_counts = {}  # po_code -> inflowReceiptsCount, for step 3
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {
            ex.submit(fetch_po_detail, session, token, facility, code): (facility, code)
            for code, facility in codes_to_refresh.items()
        }
        for fut in as_completed(futs):
            facility, code = futs[fut]
            po = fut.result()
            if po and po.get("vendorCode") == VENDOR_CODE:
                row, inflow_count, item_rows = build_po_row(facility, code, po)
                po_rows.append(row)
                po_item_rows.extend(item_rows)
                if inflow_count > 0:
                    inflow_counts[code] = facility

    upsert_rows(session, supabase_url, supabase_key, "purchase_orders", "po_code", po_rows)
    upsert_rows(session, supabase_url, supabase_key, "po_items", "po_code,item_sku", po_item_rows)

    # 3. GRNs for any PO refreshed this run that has at least one receipt.
    grn_rows = []
    grn_item_rows = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {
            ex.submit(fetch_grns_for_po, session, token, facility, code): code
            for code, facility in inflow_counts.items()
        }
        for fut in as_completed(futs):
            headers, items = fut.result()
            grn_rows.extend(headers)
            grn_item_rows.extend(items)

    upsert_rows(session, supabase_url, supabase_key, "grns", "grn_code", grn_rows)
    upsert_rows(session, supabase_url, supabase_key, "grn_items", "grn_code,item_sku", grn_item_rows)

    # Always touch a heartbeat file (with a fresh timestamp, so it always
    # differs) so the GitHub Actions workflow always has something to
    # commit. Without at least occasional commits, GitHub auto-disables
    # scheduled workflows after 60 days of no repository activity.
    with open(HEARTBEAT_PATH, "w") as f:
        f.write(datetime.now(IST).isoformat() + "\n")

    print(f"Refreshed {len(po_rows)} PO(s) ({len(po_item_rows)} line items), "
          f"{len(grn_rows)} GRN(s) ({len(grn_item_rows)} line items) this run "
          f"({'first run / full backfill' if is_first_run else 'incremental'}).")


if __name__ == "__main__":
    main()
