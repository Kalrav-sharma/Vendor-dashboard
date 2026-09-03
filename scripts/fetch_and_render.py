#!/usr/bin/env python3
"""
Pulls Lexcru Water Tech Pvt Ltd purchase orders from Uniware across the 5
Native facilities (created Aug 1 2026 onward), and renders a static HTML
dashboard to docs/index.html for GitHub Pages.

Designed to run unattended on a schedule (GitHub Actions cron, every 5 min)
with zero human/LLM involvement per run. Credentials come from environment
variables (UNIWARE_USERNAME / UNIWARE_PASSWORD) — set as GitHub Actions
repo secrets, never committed.

Cost-control design (this runs every 5 minutes, forever):
  - docs/data.json is the persisted state. On each run we load it, then:
      1. Re-fetch full detail ONLY for POs whose last known status is not
         terminal (COMPLETE/REJECTED/CANCELLED/CLOSED) — open POs can still
         change (partial receipts, approval, rejection).
      2. Search only a short recent window (RECENT_WINDOW_DAYS) per facility
         for newly created PO codes, rather than re-listing the entire
         Aug-1-to-today range every run.
      3. Terminal POs already in state are carried forward unchanged — no
         re-fetch, no re-verification.
  - This bounds each run's Uniware API calls to "however many POs are still
    open" + "5 cheap recent-window searches", not "the ever-growing full
    history".

Always writes docs/data.json with a fresh last_refreshed_at timestamp (even
if PO data didn't change), so the GitHub Actions workflow always has
something to commit — this keeps the repository "active" and prevents
GitHub's automatic disabling of stale scheduled workflows.
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))
BASE_URL = "https://urbanclap.unicommerce.com"
REQUEST_TIMEOUT = 30

FACILITIES = ["PB-UC-GGN", "PB-UC-KOL", "PB-UC-BLR", "PB-UC-BOMBAY", "PB-UC-HYD"]
VENDOR_CODE = "Vendor-156"  # LEXCRU WATER TECH PVT LTD
VENDOR_NAME = "LEXCRU WATER TECH PVT LTD"

START_DATE = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
RECENT_WINDOW_DAYS = 4  # how far back to re-search for newly created PO codes each run
TERMINAL_STATUSES = {"COMPLETE", "REJECTED", "CANCELLED", "CLOSED"}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
DOCS_DIR = os.path.join(REPO_DIR, "docs")
DATA_PATH = os.path.join(DOCS_DIR, "data.json")
HTML_PATH = os.path.join(DOCS_DIR, "index.html")


def get_access_token():
    username = os.environ.get("UNIWARE_USERNAME")
    password = os.environ.get("UNIWARE_PASSWORD")
    if not username or not password:
        sys.exit("Missing UNIWARE_USERNAME / UNIWARE_PASSWORD environment variables.")
    try:
        resp = requests.get(
            f"{BASE_URL}/oauth/token",
            params={
                "grant_type": "password",
                "client_id": "my-trusted-client",
                "username": username,
                "password": password,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        sys.exit(f"Auth request failed (network error): {type(e).__name__}")
    if not resp.ok:
        sys.exit(f"Auth request failed with HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if "access_token" not in data:
        sys.exit(f"Authentication failed: {data}")
    return data["access_token"]


def headers(token, facility_code):
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
            f"{BASE_URL}/services/rest/v1/purchase/purchaseOrder/getPurchaseOrders",
            headers=headers(token, facility),
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        return set(data.get("purchaseOrderCodes") or [])
    except Exception as e:
        print(f"WARN: PO search failed for {facility}: {e}", file=sys.stderr)
        return set()


def fetch_po_detail(session, token, facility, code):
    try:
        r = session.post(
            f"{BASE_URL}/services/rest/v1/purchase/purchaseOrder/getPurchaseOrderDetails",
            headers=headers(token, facility),
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
            f"{BASE_URL}/services/rest/v1/purchase/inflowReceipt/getInflowReceipts",
            headers=headers(token, facility),
            json={"purchaseOrderCode": po_code},
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        return data.get("inflowReceiptCodes") or []
    except Exception as e:
        print(f"WARN: GRN code search failed for {facility}/{po_code}: {e}", file=sys.stderr)
        return []


def fetch_grn_detail(session, token, facility, grn_code):
    try:
        r = session.post(
            f"{BASE_URL}/services/rest/v1/purchase/inflowReceipt/getInflowReceipt",
            headers=headers(token, facility),
            json={"inflowReceiptCode": grn_code},
            timeout=REQUEST_TIMEOUT,
        )
        d = r.json()
        ir = d.get("inflowReceipt", d)
        created_ms = ir.get("created")
        created_iso = None
        if created_ms:
            try:
                created_iso = datetime.fromtimestamp(int(created_ms) / 1000, tz=IST).isoformat()
            except (TypeError, ValueError, OSError):
                created_iso = None
        return {
            "grn_code": ir.get("code"),
            "status": ir.get("statusCode"),
            "created_iso": created_iso,
            "vendor_invoice_number": ir.get("vendorInvoiceNumber"),
            "vendor_invoice_date": ir.get("vendorInvoiceDate"),
            "total_received_amount": ir.get("totalReceivedAmount"),
            "total_rejected_amount": ir.get("totalRejectedAmount"),
        }
    except Exception as e:
        print(f"WARN: GRN detail fetch failed for {facility}/{grn_code}: {e}", file=sys.stderr)
        return None


def fetch_grns_for_po(session, token, facility, po_code):
    """Look up every GRN raised against one PO and return their details."""
    grns = []
    for grn_code in fetch_grn_codes_for_po(session, token, facility, po_code):
        detail = fetch_grn_detail(session, token, facility, grn_code)
        if detail:
            grns.append(detail)
    return grns


def build_record(facility, code, po):
    items = po.get("purchaseOrderItems") or []
    created_ms = po.get("created") or po.get("createdDate")
    created_iso = None
    if created_ms:
        try:
            created_iso = datetime.fromtimestamp(int(created_ms) / 1000, tz=IST).isoformat()
        except (TypeError, ValueError, OSError):
            created_iso = None
    return {
        "facility": facility,
        "po_code": code,
        "vendor_code": po.get("vendorCode"),
        "status": po.get("statusCode"),
        "created_iso": created_iso,
        "total_amount": (po.get("purchaseOrderPriceSummary") or {}).get("totalAmount"),
        "inflow_receipts_count": po.get("inflowReceiptsCount"),
        "qty_ordered": sum(it.get("quantity", 0) or 0 for it in items),
        "qty_received": sum(it.get("receivedQuantity", 0) or 0 for it in items),
        "qty_pending": sum(it.get("pendingQuantity", 0) or 0 for it in items),
        "qty_rejected": sum(it.get("rejectedQuantity", 0) or 0 for it in items),
        "num_items": len(items),
        "grns": [],  # filled in by main() when inflow_receipts_count > 0
    }


def load_prior_state():
    if not os.path.isfile(DATA_PATH):
        return {}
    try:
        with open(DATA_PATH) as f:
            data = json.load(f)
        return {rec["po_code"]: rec for rec in data.get("purchase_orders", [])}
    except (json.JSONDecodeError, KeyError, OSError):
        return {}


def render_html(records, generated_at_iso):
    open_recs = [r for r in records if r["status"] not in TERMINAL_STATUSES]
    total_ordered = sum(r["qty_ordered"] for r in records)
    total_received = sum(r["qty_received"] for r in records)
    total_pending = sum(r["qty_pending"] for r in records)
    total_rejected_qty = sum(r["qty_rejected"] for r in records)
    total_amount = sum(r["total_amount"] or 0 for r in records)
    total_grn_received_amount = sum(
        g.get("total_received_amount") or 0 for r in records for g in (r.get("grns") or [])
    )
    rejected_pos = [r for r in records if r["status"] == "REJECTED"]

    status_counts = {}
    for r in records:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1

    def fmt_num(n):
        return f"{n:,.0f}" if isinstance(n, (int, float)) else "-"

    def fmt_money(n):
        return f"₹{n:,.0f}" if isinstance(n, (int, float)) else "-"

    def status_badge(status):
        cls = {
            "COMPLETE": "st-complete",
            "REJECTED": "st-rejected",
            "APPROVED": "st-approved",
            "CREATED": "st-created",
        }.get(status, "st-other")
        return f'<span class="badge {cls}">{status or "UNKNOWN"}</span>'

    def grn_summary(r):
        grns = r.get("grns") or []
        if not grns:
            return "-", "-"
        invoices = ", ".join(sorted({g["vendor_invoice_number"] for g in grns if g.get("vendor_invoice_number")})) or "-"
        recv_total = sum(g.get("total_received_amount") or 0 for g in grns)
        rej_total = sum(g.get("total_rejected_amount") or 0 for g in grns)
        amount_str = fmt_money(recv_total)
        if rej_total:
            amount_str += f" <span class='rej-amt'>(-{fmt_money(rej_total)} rej)</span>"
        return invoices, amount_str

    rows = []
    for r in sorted(records, key=lambda x: x["created_iso"] or "", reverse=True):
        invoices, grn_amount = grn_summary(r)
        rows.append(f"""
        <tr>
          <td>{r['facility']}</td>
          <td class="mono">{r['po_code']}</td>
          <td>{status_badge(r['status'])}</td>
          <td>{(r['created_iso'] or '')[:16].replace('T', ' ')}</td>
          <td class="num">{fmt_num(r['qty_ordered'])}</td>
          <td class="num">{fmt_num(r['qty_received'])}</td>
          <td class="num">{fmt_num(r['qty_pending'])}</td>
          <td class="num">{fmt_num(r['total_amount'])}</td>
          <td class="mono">{invoices}</td>
          <td class="num">{grn_amount}</td>
        </tr>""")

    status_pills = "".join(
        f'<div class="pill">{status_badge(s)} <span class="pill-count">{c}</span></div>'
        for s, c in sorted(status_counts.items(), key=lambda kv: -kv[1])
    )

    alert_html = ""
    if rejected_pos:
        items = "".join(
            f"<li>{r['facility']} · {r['po_code']} · {fmt_money(r['total_amount'])} · {fmt_num(r['qty_ordered'])} units</li>"
            for r in rejected_pos
        )
        alert_html = f"""
        <div class="alert">
          <strong>{len(rejected_pos)} rejected PO(s)</strong> worth {fmt_money(sum(r['total_amount'] or 0 for r in rejected_pos))} total
          <ul>{items}</ul>
        </div>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lexcru PO Tracker</title>
<style>
  :root {{
    --bg: #f7f7f5; --card: #ffffff; --text: #1a1a1a; --muted: #6b6b6b;
    --border: #e4e4e0; --accent: #2563eb;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #16161a; --card: #1e1e24; --text: #f0f0ee; --muted: #9a9a9e; --border: #2e2e35; --accent: #60a5fa; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 24px 16px 60px; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 2px; }}
  .sub {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 20px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
  .card .label {{ font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 4px; }}
  .card .value {{ font-size: 1.35rem; font-weight: 600; }}
  .pills {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }}
  .pill {{ display: flex; align-items: center; gap: 6px; background: var(--card); border: 1px solid var(--border); border-radius: 999px; padding: 5px 10px; font-size: 0.8rem; }}
  .pill-count {{ font-weight: 600; }}
  .badge {{ font-size: 0.72rem; font-weight: 600; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }}
  .st-complete {{ background: #dcfce7; color: #166534; }}
  .st-approved {{ background: #dbeafe; color: #1e40af; }}
  .st-rejected {{ background: #fee2e2; color: #991b1b; }}
  .st-created {{ background: #fef9c3; color: #854d0e; }}
  .st-other {{ background: #f0f0f0; color: #444; }}
  @media (prefers-color-scheme: dark) {{
    .st-complete {{ background: #14301f; color: #86efac; }}
    .st-approved {{ background: #16233f; color: #93c5fd; }}
    .st-rejected {{ background: #3a1717; color: #fca5a5; }}
    .st-created {{ background: #3a331a; color: #fde68a; }}
    .st-other {{ background: #2a2a2a; color: #ccc; }}
  }}
  .alert {{ background: #fef2f2; border: 1px solid #fecaca; color: #7f1d1d; border-radius: 10px; padding: 12px 16px; margin-bottom: 20px; font-size: 0.88rem; }}
  @media (prefers-color-scheme: dark) {{
    .alert {{ background: #2a1414; border-color: #5c1f1f; color: #fca5a5; }}
  }}
  .alert ul {{ margin: 6px 0 0; padding-left: 18px; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-weight: 600; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em; }}
  tr:last-child td {{ border-bottom: none; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .mono {{ font-family: ui-monospace, monospace; font-size: 0.8rem; }}
  .rej-amt {{ color: #b91c1c; font-size: 0.75rem; }}
  @media (prefers-color-scheme: dark) {{ .rej-amt {{ color: #f87171; }} }}
  .table-wrap {{ overflow-x: auto; }}
  footer {{ color: var(--muted); font-size: 0.75rem; margin-top: 18px; text-align: center; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Lexcru Water Tech — PO Tracker</h1>
  <div class="sub">Vendor {VENDOR_CODE} ({VENDOR_NAME}) · Facilities: {', '.join(FACILITIES)} · POs created {START_DATE.date()} onward · Auto-refreshed every 5 min</div>

  {alert_html}

  <div class="cards">
    <div class="card"><div class="label">Total POs</div><div class="value">{len(records)}</div></div>
    <div class="card"><div class="label">Open POs</div><div class="value">{len(open_recs)}</div></div>
    <div class="card"><div class="label">Qty Ordered</div><div class="value">{fmt_num(total_ordered)}</div></div>
    <div class="card"><div class="label">Qty Received</div><div class="value">{fmt_num(total_received)}</div></div>
    <div class="card"><div class="label">Qty Pending</div><div class="value">{fmt_num(total_pending)}</div></div>
    <div class="card"><div class="label">Total Value</div><div class="value">{fmt_money(total_amount)}</div></div>
    <div class="card"><div class="label">GRN Amount Received</div><div class="value">{fmt_money(total_grn_received_amount)}</div></div>
  </div>

  <div class="pills">{status_pills}</div>

  <div class="table-wrap">
  <table>
    <thead><tr>
      <th>Facility</th><th>PO Code</th><th>Status</th><th>Created (IST)</th>
      <th class="num">Qty Ord</th><th class="num">Qty Recv</th><th class="num">Qty Pend</th><th class="num">PO Amount</th>
      <th>Invoice No(s)</th><th class="num">GRN Amount</th>
    </tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  </div>

  <footer>Last refreshed: {generated_at_iso} · Rejected units so far: {fmt_num(total_rejected_qty)}</footer>
</div>
</body>
</html>"""


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)
    token = get_access_token()
    session = requests.Session()

    prior = load_prior_state()
    now_utc = datetime.now(timezone.utc)
    is_first_run = not prior
    search_start = START_DATE if is_first_run else now_utc - timedelta(days=RECENT_WINDOW_DAYS)

    # 1. Find candidate PO codes per facility: known-open ones (from prior
    #    state) + anything created in the search window (catches new POs).
    #    First run ever (no docs/data.json yet) backfills the FULL range
    #    since START_DATE, chunked to dodge Uniware's wide-date-range gotcha;
    #    every subsequent run only re-searches the short recent window.
    codes_to_refresh = {}  # code -> facility
    for facility in FACILITIES:
        cursor = search_start
        recent_codes = set()
        while cursor < now_utc:
            chunk_end = min(cursor + timedelta(days=14), now_utc)
            recent_codes |= search_po_codes(session, token, facility, cursor, chunk_end)
            cursor = chunk_end
        for code in recent_codes:
            codes_to_refresh[code] = facility

    for code, rec in prior.items():
        if rec.get("status") not in TERMINAL_STATUSES:
            codes_to_refresh.setdefault(code, rec["facility"])

    # 2. Fetch fresh detail for all candidates, in parallel.
    fresh_records = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {
            ex.submit(fetch_po_detail, session, token, facility, code): (facility, code)
            for code, facility in codes_to_refresh.items()
        }
        for fut in as_completed(futs):
            facility, code = futs[fut]
            po = fut.result()
            if po and po.get("vendorCode") == VENDOR_CODE:
                fresh_records[code] = build_record(facility, code, po)

    # 2b. For every freshly-refreshed PO that has at least one GRN raised
    #     against it, fetch those GRNs' invoice numbers and amounts. Only
    #     runs for POs we already decided to refresh above (open POs, or
    #     newly created ones) — terminal POs carried forward from prior
    #     state keep whatever GRN data they already have, no re-fetch.
    grn_targets = [
        (rec["facility"], code)
        for code, rec in fresh_records.items()
        if (rec.get("inflow_receipts_count") or 0) > 0
    ]
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {
            ex.submit(fetch_grns_for_po, session, token, facility, code): code
            for facility, code in grn_targets
        }
        for fut in as_completed(futs):
            code = futs[fut]
            fresh_records[code]["grns"] = fut.result()

    # 3. Merge: fresh data wins; terminal POs from prior state not touched
    #    this run are carried forward unchanged.
    merged = dict(prior)
    merged.update(fresh_records)
    # Drop any prior record whose facility isn't in current scope (defensive).
    merged = {k: v for k, v in merged.items() if v.get("facility") in FACILITIES}

    records = list(merged.values())
    generated_at_iso = datetime.now(IST).isoformat()

    with open(DATA_PATH, "w") as f:
        json.dump({
            "generated_at": generated_at_iso,
            "vendor_code": VENDOR_CODE,
            "vendor_name": VENDOR_NAME,
            "facilities": FACILITIES,
            "start_date": START_DATE.date().isoformat(),
            "purchase_orders": records,
        }, f, indent=2, default=str)

    with open(HTML_PATH, "w") as f:
        f.write(render_html(records, generated_at_iso))

    print(f"Refreshed {len(fresh_records)} PO(s) this run; {len(records)} total in scope.")


if __name__ == "__main__":
    main()
