#!/usr/bin/env python3
"""
Spares Projections (beta) step 2 of 2: demand + live stock -> per-vendor order projections
-> public.spares_projection.

Same method as the spares-vendor-order-mailer agent (the numbers vendors are emailed), but fed from
Supabase so it needs no VPN and re-runs after every spares sync:
  demand      spares_forecast_demand   5 monthly SKU-total units (uploaded by sync_spares_forecast_demand.py)
  on-hand     spares_wh_inventory      Uniware good_qty at the 5 mother warehouses (BLR/GGN/HYD/KOL/BOMBAY;
                                       Pataudi / Panchla excluded, as in the mailer's Snowflake query)
  in-transit  spares_sku_master        sheet "In transit" columns, 5 warehouses summed
  vendor      sheet "Spares Base Data" col D (case-insensitive), canonical name from "Vendor Master";
              fallback = the refresh/IK BOM vendor from the demand upload; else NULL (= Unassigned)

Rules (RULES below; the first 3 months are the projection, months 4-5 are look-ahead):
  rolling_coverage   order-driven SKUs (and IK until its onboarding projection is loaded):
                       cover = on_hand + in_transit
                       per month i: leftover = max(0, cover - d[i]); order = max(0, d[i+1] + d[i+2] - leftover)
                                    cover = leftover + order
  net_against_cover  Refresh children / live IK (demand already lead-shifted, NO forward buffer):
                       order = max(0, d[i] - cover); cover = cover + order - d[i]
No MOQ, no open-PO term, whole units. Wholesale-replaces spares_projection each run.

Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GOOGLE_SERVICE_ACCOUNT_JSON.

  python3 scripts/sync_spares_projection.py            # compute + write
  python3 scripts/sync_spares_projection.py --dry-run  # compute + print summary, no write
  --csv PATH   also dump the computed rows to PATH (for checking / tuning the rules)
"""
import datetime
import os
import sys

import requests

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import get_access_token, replace_by_filter, supabase_config  # noqa: E402

SHEET_ID = "1Eb8fqROZLM2sw2_6GbWo75AqtzPK5dvTVc1vwV8FOz0"  # Spare automations
MOTHER_WAREHOUSES = ("PB-UC-BLR", "PB-UC-GGN", "PB-UC-HYD", "PB-UC-KOL", "PB-UC-BOMBAY")
RULES = {"order": "rolling_coverage", "ik": "net_against_cover", "refresh": "net_against_cover"}
SHEETS_TIMEOUT = 300  # this workbook is large; a bounded-range read can take minutes


def project(d, cover, rule):
    """d = 5 monthly demands, cover = on_hand + in_transit -> 3 monthly order quantities."""
    out = []
    for i in range(3):
        if rule == "rolling_coverage":
            leftover = max(0, cover - d[i])
            order = max(0, d[i + 1] + d[i + 2] - leftover)
            cover = leftover + order
        else:  # net_against_cover
            order = max(0, d[i] - cover)
            cover = cover + order - d[i]
        out.append(int(round(order)))
    return out


def sb_get(url, key, table, select, page=1000):
    rows, start = [], 0
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    while True:
        r = requests.get(f"{url}/rest/v1/{table}", params={"select": select, "order": "sku", "limit": page, "offset": start},
                         headers=h, timeout=60)
        r.raise_for_status()
        batch = r.json()
        rows += batch
        if len(batch) < page:
            return rows
        start += page


def sheet_range(token, tab, rng):
    last = None
    for _ in range(2):
        try:
            r = requests.get(f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/'{tab}'!{rng}",
                             headers={"Authorization": f"Bearer {token}"}, timeout=SHEETS_TIMEOUT)
            r.raise_for_status()
            return r.json().get("values", [])
        except requests.RequestException as e:
            last = e
    sys.exit(f"Sheets read of {tab} failed: {last}")


norm = lambda s: (s or "").strip().lower()


def main():
    dry = "--dry-run" in sys.argv
    url, key = supabase_config()

    demand_rows = sb_get(url, key, "spares_forecast_demand",
                         "sku,month_start,demand,demand_driver,bom_vendor,forecast_asof")
    if not demand_rows:
        sys.exit("spares_forecast_demand is empty -- run sync_spares_forecast_demand.py on the VPN laptop first.")
    by_sku = {}
    for r in demand_rows:
        by_sku.setdefault(r["sku"], []).append(r)
    asof = max(r["forecast_asof"] for r in demand_rows)

    inv = sb_get(url, key, "spares_wh_inventory", "sku,facility,good_qty")
    on_hand = {}
    for r in inv:
        if r["facility"] in MOTHER_WAREHOUSES:
            on_hand[r["sku"]] = on_hand.get(r["sku"], 0) + float(r["good_qty"] or 0)
    master = {r["sku"]: r for r in sb_get(url, key, "spares_sku_master",
              "sku,in_transit_ggn,in_transit_blr,in_transit_bom,in_transit_kol,in_transit_hyd")}

    token = get_access_token()
    base = sheet_range(token, "Spares Base Data", "A1:F700")
    vm = sheet_range(token, "Vendor Master", "A1:J80")
    canon = {norm(r[1]): r[1].strip() for r in vm[1:] if len(r) > 1 and r[1].strip()}
    base_vendor = {r[0].strip().lower(): r[3].strip() for r in base[1:] if r and r[0].strip() and len(r) > 3 and r[3].strip()}
    NA = {"", "n/a", "na", "#n/a"}

    def vendor_of(sku, bom_vendor):
        for cand in (base_vendor.get(sku.lower()), bom_vendor):
            if cand and norm(cand) not in NA:
                return canon.get(norm(cand), cand)
        return None

    out = []
    for sku, rs in by_sku.items():
        rs.sort(key=lambda r: r["month_start"])
        if len(rs) < 5:
            print(f"SKIP {sku}: only {len(rs)} forecast months", file=sys.stderr)
            continue
        d = [float(r["demand"]) for r in rs]
        driver = rs[0]["demand_driver"]
        rule = RULES.get(driver, "rolling_coverage")
        oh = on_hand.get(sku, 0.0)
        m = master.get(sku) or {}
        it = sum(float(m.get(c) or 0) for c in ("in_transit_ggn", "in_transit_blr", "in_transit_bom", "in_transit_kol", "in_transit_hyd"))
        orders = project(d, oh + it, rule)
        vendor = vendor_of(sku, rs[0]["bom_vendor"])
        for i in range(3):
            out.append({"sku": sku, "month_start": rs[i]["month_start"], "vendor": vendor, "order_qty": orders[i],
                        "demand": d[i], "on_hand": oh, "in_transit": it, "rule": rule, "demand_driver": driver,
                        "forecast_asof": asof})

    n_sku = len(out) // 3
    unassigned = sorted({o["sku"] for o in out if not o["vendor"]})
    vendors = {}
    for o in out:
        if o["vendor"]:
            vendors[o["vendor"]] = vendors.get(o["vendor"], 0) + o["order_qty"]
    print(f"forecast as-of {asof}: {n_sku} SKU(s), {len(out)} row(s); {len(vendors)} vendor(s); "
          f"total 3-month order {sum(vendors.values()):,}; unassigned SKUs: {len(unassigned)} {unassigned}")
    for v, q in sorted(vendors.items(), key=lambda kv: -kv[1]):
        print(f"  {v:22s} {q:>10,}")
    if "--csv" in sys.argv:
        import csv
        with open(sys.argv[sys.argv.index("--csv") + 1], "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
    if dry:
        print("--dry-run: nothing written.")
        return
    replace_by_filter(url, key, "spares_projection", out, {"sku": "not.is.null"})
    print(f"Synced {len(out)} spares_projection row(s) at {datetime.datetime.utcnow().isoformat()}Z.")


if __name__ == "__main__":
    main()
