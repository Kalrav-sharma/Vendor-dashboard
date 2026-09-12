#!/usr/bin/env python3
"""
S&OP: Channel Dispatch Plan tab.

Direct port of ~/.claude/scripts/parse_channel_dispatch_plan.js's
computeForDate(): for 7 rolling horizons (+7/+15/+21/+30/+45/+60/+90 days)
plus 1 pinned calendar date (read from sop_dispatch_pinned_date, updated
manually via SQL when the business moves it), projects closing inventory
per channel x SKU against 4 parallel DOI targets (30/15/7/0 days) and
reports required dispatch, then checks that against planned production.

CRITICAL INVARIANT, preserved exactly from the source script: the 5-
warehouse view is computed FIRST, each warehouse's required_dispatch
clamped INDEPENDENTLY at 0 (a warehouse's own surplus never offsets
another's deficit) -- UC App+PLS's channel-level required_dispatch is
then the SUM of those 5 already-clamped warehouse values, never a
separately-computed pooled network figure. Do not "simplify" this by
computing UC App+PLS's channel row directly from network totals.

Credentials: GOOGLE_SERVICE_ACCOUNT_JSON, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY (all already provisioned, no new secrets).
"""
import datetime
import sys
import zoneinfo

import requests

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import (  # noqa: E402
    SKUS, WH_CHANNEL_SKU_ID, get_access_token, get_values, normalize_date, normalize_sku, pad_row,
    replace_by_filter, supabase_config, to_num,
)
from sync_sop_production import parse_daily_production  # noqa: E402

CHANNELS = ["UC App + PLS", "Amazon", "Flipkart", "MT"]
DOI_TARGETS = [30, 15, 7, 0]
PRODUCTION_LEAD_DAYS = 5
HORIZON_DAYS = [7, 15, 21, 30, 45, 60, 90]
DOI_PROJECTION_MAX_DAYS = 400

WAREHOUSES = ["Bangalore", "Gurgaon", "Hyderabad", "Mumbai", "Kolkata"]
WH_SPLIT = {"Bangalore": 0.25, "Gurgaon": 0.23, "Hyderabad": 0.23, "Mumbai": 0.20, "Kolkata": 0.09}
WH_CODE_MAP = {
    "PB-UC-BLR": "Bangalore", "PB-UC-BOMBAY": "Mumbai", "PB-UC-MUM": "Mumbai", "PB-UC-BOM": "Mumbai",
    "PB-UC-GGN": "Gurgaon", "PB-UC-GGN-PATAUDI": "Gurgaon", "PB-UC-GGN_PATAUDI": "Gurgaon",
    "PB-UC-HYD": "Hyderabad", "PB-UC-KOL": "Kolkata", "PB-UC-KOL-PANCHLA": "Kolkata",
    "PB-UC-KOL-PANCHALA": "Kolkata",
}
RDH_DATE_COL, RDH_ORIGIN_COL, RDH_MOVTYPE_COL, RDH_CHANNEL_COL = 1, 4, 8, 12
RDH_SKU_COLS = {"M0": 18, "M1-2nd Gen": 19, "M2 Pro": 21, "M1 Pro": 22, "M3 Pro": 23, "M3": 24}
PO_CHANNEL_BUCKET = {"Amazon": "Amazon", "Primarc": "Amazon", "Flipkart": "Flipkart",
                     "Croma": "MT", "Vijay Sales": "MT", "Reliance": "MT"}
MONTH_ABBR = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
CURRENT_YEAR = datetime.date.today().year


def add_days_ymd(ymd, days):
    return (datetime.date.fromisoformat(ymd) + datetime.timedelta(days=days)).isoformat()


def channel_bucket(chan_raw):
    c = str(chan_raw or "").strip().upper()
    if "UC APP" in c or "UC-APP" in c:
        return "UC App + PLS"
    if c == "AMAZON":
        return "Amazon"
    if c == "FLIPKART":
        return "Flipkart"
    if c in ("CROMA", "VIJAY SALES", "RELIANCE"):
        return "MT"
    return None


def parse_current_inventory_by_channel(rows):
    """Port of parseCurrentInventoryByChannel(): 4-channel bucket totals + UC App+PLS's per-city
    breakdown, with the defensive re-spelled-header detection for a second unrelated table
    sometimes pasted mid-block with no Channel label of its own."""
    header_row, channel_col, city_col = -1, -1, -1
    sku_col_map = {}
    for i in range(min(20, len(rows))):
        row = rows[i]
        sku_count = 0
        for c, cell in enumerate(row):
            s = str(cell or "").strip()
            sl = s.lower()
            if sl == "channel":
                channel_col = c
            if sl in ("city", "location"):
                city_col = c
            sku = normalize_sku(s)
            if sku and sku not in sku_col_map:
                sku_col_map[sku] = c
                sku_count += 1
        if sku_count >= 3 and channel_col != -1:
            header_row = i
            break
    by_channel = {ch: {s: 0.0 for s in SKUS} for ch in CHANNELS}
    by_city_uc = {wh: {s: 0.0 for s in SKUS} for wh in WAREHOUSES}
    if header_row == -1:
        print("WARNING: could not find on-hand header in Current Inventory", file=sys.stderr)
        return by_channel, by_city_uc
    if city_col == -1:
        city_col = 1

    current_channel = ""
    for i in range(header_row + 1, len(rows)):
        row = rows[i]
        channel_val = str(pad_row(row, channel_col + 1)[channel_col] or "").strip()
        city_val = str(pad_row(row, city_col + 1)[city_col] or "").strip()
        if channel_val:
            current_channel = channel_val
        if not city_val:
            continue
        sku_cols_matching_own_label = [s for s in SKUS
                                        if sku_col_map.get(s) is not None
                                        and normalize_sku(pad_row(row, sku_col_map[s] + 1)[sku_col_map[s]]) == s]
        if len(sku_cols_matching_own_label) >= 3:
            current_channel = ""
            continue
        if city_val.lower() == "grand total":
            continue
        bucket = channel_bucket(current_channel)
        if not bucket:
            continue
        if city_val.lower() == "delhi":
            continue
        for sku in SKUS:
            col = sku_col_map.get(sku)
            if col is None or col >= len(row):
                continue
            qty = to_num(row[col])
            by_channel[bucket][sku] += qty
            if bucket == "UC App + PLS" and city_val in by_city_uc:
                by_city_uc[city_val][sku] += qty
    return by_channel, by_city_uc


def parse_in_transit_from_current_inventory(rows):
    """Port of parseInTransitFromCurrentInventory(): finds the literal 'in transit inventory' label,
    reads the header 2 rows below it (label, blank spacer, header), collects the leftmost 'To' +
    following SKU columns, stops at 'Grand Total'. Returns (network_total, by_city)."""
    network_total = {s: 0.0 for s in SKUS}
    by_city = {wh: {s: 0.0 for s in SKUS} for wh in WAREHOUSES}
    label_row = -1
    for i, row in enumerate(rows):
        if any(str(c or "").strip().lower() == "in transit inventory" for c in row):
            label_row = i
            break
    if label_row == -1:
        print('WARNING: could not find "In transit Inventory" block in Current Inventory tab', file=sys.stderr)
        return network_total, by_city

    header_row = rows[label_row + 2] if label_row + 2 < len(rows) else []
    fac_col = -1
    it_sku_cols = {}
    for c, cell in enumerate(header_row):
        s = str(cell or "").strip()
        if fac_col == -1 and s.upper() == "TO":
            fac_col = c
            continue
        sku = normalize_sku(s)
        if sku and fac_col != -1 and sku not in it_sku_cols:
            it_sku_cols[sku] = c
    if fac_col == -1:
        print('WARNING: could not find "To" facility column in In transit Inventory header', file=sys.stderr)
        return network_total, by_city

    for i in range(label_row + 3, len(rows)):
        row = rows[i]
        fac_val = str(pad_row(row, fac_col + 1)[fac_col] or "").strip().upper()
        if not fac_val:
            continue
        if fac_val == "GRAND TOTAL":
            break
        if not fac_val.startswith("PB-UC"):
            continue
        city = WH_CODE_MAP.get(fac_val)
        for sku in SKUS:
            col = it_sku_cols.get(sku)
            if col is None or col >= len(row):
                continue
            qty = to_num(row[col])
            network_total[sku] += qty
            if city:
                by_city[city][sku] += qty
    return network_total, by_city


def parse_raw_data_helper(rows):
    """Port of parseRawDataHelper(): every PO record (movement type MM, recognized channel bucket)
    in WH-Channel-SKU's own Raw data helper tab."""
    records = []
    for row in rows[1:]:
        row = pad_row(row, max(RDH_SKU_COLS.values()) + 1)
        ymd = normalize_date(row[RDH_DATE_COL], default_year=CURRENT_YEAR)
        if not ymd:
            continue
        if str(row[RDH_MOVTYPE_COL] or "").strip() != "MM":
            continue
        channel_raw = str(row[RDH_CHANNEL_COL] or "").strip()
        bucket = PO_CHANNEL_BUCKET.get(channel_raw)
        if not bucket:
            continue
        by_sku = {}
        for sku, col in RDH_SKU_COLS.items():
            qty = to_num(row[col])
            if qty > 0:
                by_sku[sku] = qty
        if by_sku:
            records.append({"ymd": ymd, "channel": bucket, "origin": str(row[RDH_ORIGIN_COL] or "").strip(),
                             "by_sku": by_sku})
    return records


def sum_po_outflow_by_warehouse(records, today_ymd, target_ymd):
    outflow = {wh: {s: 0.0 for s in SKUS} for wh in WAREHOUSES}
    for rec in records:
        if rec["ymd"] < today_ymd or rec["ymd"] > target_ymd:
            continue
        wh = WH_CODE_MAP.get(rec["origin"].upper())
        if not wh:
            continue
        for sku, qty in rec["by_sku"].items():
            outflow[wh][sku] += qty
    return outflow


def parse_daily_trackr_tab(rows, date_col, day_col_start):
    """Port of parseDailyTrackrTab(): generic reader for the 4 single-channel daily tabs. A row's date
    cell must parse (via normalize_date, handling both 'Jun 1 2026' and 'D-MMM' forms seen across
    these tabs); a date row present but every SKU cell blank is treated as no data for that date."""
    series = {}
    min_date, max_date = None, None
    for i in range(2, len(rows)):
        row = rows[i]
        date_cell = row[date_col] if date_col < len(row) else None
        ymd = normalize_date(date_cell, default_year=CURRENT_YEAR)
        if not ymd:
            continue
        raw = [row[c] if c < len(row) else None for c in range(day_col_start, day_col_start + 6)]
        if not any(v not in (None, "") for v in raw):
            continue
        by_sku = {"M0": to_num(raw[0]), "M1-2nd Gen": to_num(raw[1]), "M1 Pro": to_num(raw[2]),
                  "M2 Pro": to_num(raw[3]), "M3": to_num(raw[4]), "M3 Pro": to_num(raw[5])}
        series[ymd] = by_sku
        if min_date is None or ymd < min_date:
            min_date = ymd
        if max_date is None or ymd > max_date:
            max_date = ymd
    return {"series": series, "min_date": min_date, "max_date": max_date}


def sum_daily_series_window(series, start_ymd, num_days, include_start_day=False):
    result = {s: 0.0 for s in SKUS}
    dates_missing = []
    for d in range(0 if include_start_day else 1, num_days + 1):
        ymd = add_days_ymd(start_ymd, d)
        entry = series.get(ymd)
        if not entry:
            dates_missing.append(ymd)
            continue
        for sku in SKUS:
            result[sku] += entry.get(sku, 0.0)
    return {"result": result, "dates_missing": dates_missing}


def ordinal_suffix(n):
    if 10 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def parse_diwali_opening_ask(rows, pinned_ymd):
    """Port of parseDiwaliOpeningAsk(): per channel, finds the '<Channel> >>' anchor, then an
    'Opening Ask for <day-after><ordinal> <MonthAbbrev>(t)' row within 15 rows below it."""
    day_after = add_days_ymd(pinned_ymd, 1)
    y, m, d = day_after.split("-")
    day_int = int(d)
    month_abbrev = MONTH_ABBR[int(m) - 1]
    day_label = f"{day_int}{ordinal_suffix(day_int)}"

    channel_anchors = {"Amazon >>": "Amazon", "Flipkart >>": "Flipkart", "MT >>": "MT"}
    out = {}
    for i, row in enumerate(rows):
        channel = channel_anchors.get(str(row[0] if row else "" or "").strip())
        if not channel:
            continue
        found = False
        for j in range(i, min(i + 15, len(rows))):
            label = str(rows[j][1] if len(rows[j]) > 1 else "").strip().lower()
            if label.startswith(f"opening ask for {day_label} {month_abbrev}"):
                by_sku = {}
                for k in range(6):
                    col = 2 + k
                    by_sku[SKUS[k]] = to_num(rows[j][col] if col < len(rows[j]) else None)
                out[channel] = by_sku
                found = True
                break
        if not found:
            print(f'WARNING: Diwali tab: found "{channel} >>" anchor but no "{day_label} {month_abbrev}(t)" '
                  f"Opening Ask row within 15 rows below it -- falling back to daily-trackr target for "
                  f"{channel}", file=sys.stderr)
    return out


def compute_forward_doi_from_series(series, max_known_ymd, start_ymd, sku, quantity, rate_multiplier=1.0):
    """Port of computeForwardDOIFromSeries(): forward walk consuming daily rate until exhausted.
    Returns a float day count, None (capped at DOI_PROJECTION_MAX_DAYS), or 'INSUFFICIENT_DATA'."""
    if not (quantity > 0):
        return 0.0
    remaining, ymd = quantity, start_ymd
    for days in range(1, DOI_PROJECTION_MAX_DAYS + 1):
        ymd = add_days_ymd(ymd, 1)
        if not max_known_ymd or ymd > max_known_ymd:
            return "INSUFFICIENT_DATA"
        daily_rate = series.get(ymd, {}).get(sku, 0.0) * rate_multiplier
        if daily_rate <= 0:
            continue
        if remaining <= daily_rate:
            return (days - 1) + remaining / daily_rate
        remaining -= daily_rate
    return None


def derive_status(target, projected_closing, required_dispatch):
    if target == 0 and projected_closing == 0:
        return "N/A"
    if projected_closing < 0:
        return "ALREADY SHORT"
    if required_dispatch > 0:
        return "NEEDS DISPATCH"
    return "ON TRACK"


def sum_production_window(actual_prod_by_date, start_ymd, end_ymd):
    totals = {s: 0.0 for s in SKUS}
    if start_ymd > end_ymd:
        return totals
    d = start_ymd
    while d <= end_ymd:
        entry = actual_prod_by_date.get(d)
        if entry:
            for sku in SKUS:
                totals[sku] += entry.get(sku, 0.0)
        d = add_days_ymd(d, 1)
    return totals


def compute_for_date(ctx, target_ymd, window_days, apply_fixed_targets=False, production_window_end_override=None):
    """Port of computeForDate(). `ctx` bundles every value parsed once and shared across horizons."""
    today_ymd = ctx["today_ymd"]

    po_inflow_by_ch = {ch: sum_daily_series_window(ctx["planned_inward"][ch], today_ymd, window_days, True)
                       for ch in ("Amazon", "Flipkart", "MT")}
    sales_expected_by_ch = {ch: sum_daily_series_window(ctx["channel_series"][ch]["series"], today_ymd, window_days, True)
                            for ch in CHANNELS}
    sales_expected_days = window_days + 1
    target_closing_by_ch_by_doi = {
        ch: {d: sum_daily_series_window(ctx["channel_series"][ch]["series"], target_ymd, d) for d in DOI_TARGETS}
        for ch in CHANNELS
    }

    prod_window_start = today_ymd
    prod_window_end = production_window_end_override or add_days_ymd(target_ymd, -PRODUCTION_LEAD_DAYS)
    has_production_window = prod_window_start <= prod_window_end
    production_planned = (sum_production_window(ctx["actual_prod_by_date"], prod_window_start, prod_window_end)
                           if has_production_window else {s: 0.0 for s in SKUS})

    uc_outflow_by_sku = {
        sku: sum(po_inflow_by_ch[ch]["result"][sku] for ch in ("Amazon", "Flipkart", "MT"))
        for sku in SKUS
    }

    # Warehouse view FIRST -- see module docstring for why this ordering and independent clamping
    # is load-bearing, not incidental.
    wh_po_outflow = sum_po_outflow_by_warehouse(ctx["po_records"], today_ymd, target_ymd)
    wh_rows = []
    for wh in WAREHOUSES:
        for sku in SKUS:
            on_hand = ctx["current_inv_by_city_uc"][wh][sku] + ctx["in_transit_by_city_uc"][wh][sku]
            po_out = -wh_po_outflow[wh][sku]
            sales_expected = sales_expected_by_ch["UC App + PLS"]["result"][sku] * WH_SPLIT[wh]
            projected_closing = on_hand + po_out - sales_expected
            doi = {}
            for d in DOI_TARGETS:
                target = target_closing_by_ch_by_doi["UC App + PLS"][d]["result"][sku] * WH_SPLIT[wh]
                required_dispatch = max(0.0, target - projected_closing)
                doi[d] = {"target": target, "required_dispatch": required_dispatch,
                          "status": derive_status(target, projected_closing, required_dispatch)}
            projected_doi = compute_forward_doi_from_series(
                ctx["channel_series"]["UC App + PLS"]["series"], ctx["channel_series"]["UC App + PLS"]["max_date"],
                target_ymd, sku, projected_closing, WH_SPLIT[wh])
            wh_rows.append({"sku": sku, "warehouse": wh, "on_hand": on_hand, "po_out": po_out,
                            "sales_expected": sales_expected, "projected_closing": projected_closing,
                            "projected_doi": projected_doi, "doi": doi})

    status_severity = ["ALREADY SHORT", "NEEDS DISPATCH", "ON TRACK", "N/A"]
    wh_total_required, wh_total_target, wh_worst_status = {}, {}, {}
    for d in DOI_TARGETS:
        wh_total_required[d], wh_total_target[d], wh_worst_status[d] = {}, {}, {}
        for sku in SKUS:
            rows_for_sku = [r for r in wh_rows if r["sku"] == sku]
            wh_total_required[d][sku] = sum(r["doi"][d]["required_dispatch"] for r in rows_for_sku)
            wh_total_target[d][sku] = sum(r["doi"][d]["target"] for r in rows_for_sku)
            statuses = [r["doi"][d]["status"] for r in rows_for_sku]
            wh_worst_status[d][sku] = next((s for s in status_severity if s in statuses), "N/A")

    rows_ = []
    for ch in CHANNELS:
        is_uc = ch == "UC App + PLS"
        for sku in SKUS:
            on_hand = ctx["current_inv"][ch][sku] + (ctx["in_transit_network"][sku] if is_uc else 0.0)
            po_in = -uc_outflow_by_sku[sku] if is_uc else po_inflow_by_ch.get(ch, {"result": {}})["result"].get(sku, 0.0)
            sales_expected = sales_expected_by_ch[ch]["result"][sku]
            projected_closing = on_hand + po_in - sales_expected

            fixed_target_for_sku = (ctx["fixed_closing_targets"].get(target_ymd, {}).get(ch, {})
                                     if apply_fixed_targets else {})
            target_is_fixed = sku in fixed_target_for_sku

            doi = {}
            for d in DOI_TARGETS:
                if target_is_fixed:
                    target = fixed_target_for_sku[sku]
                elif is_uc:
                    target = wh_total_target[d][sku]
                else:
                    target = target_closing_by_ch_by_doi[ch][d]["result"][sku]
                required_dispatch = wh_total_required[d][sku] if is_uc else max(0.0, target - projected_closing)
                status = wh_worst_status[d][sku] if is_uc else derive_status(target, projected_closing, required_dispatch)
                doi[d] = {"target": target, "required_dispatch": required_dispatch, "status": status}

            projected_doi = compute_forward_doi_from_series(
                ctx["channel_series"][ch]["series"], ctx["channel_series"][ch]["max_date"],
                target_ymd, sku, projected_closing)

            rows_.append({"sku": sku, "channel": ch, "on_hand": on_hand, "po_in": po_in,
                          "sales_expected": sales_expected, "projected_closing": projected_closing,
                          "projected_doi": projected_doi, "doi": doi})

    total_required_by_doi = {}
    for d in DOI_TARGETS:
        total_required_by_doi[d] = {sku: sum(r["doi"][d]["required_dispatch"] for r in rows_ if r["sku"] == sku)
                                     for sku in SKUS}

    production_rows_by_doi = {}
    for d in DOI_TARGETS:
        production_rows_by_doi[d] = []
        for sku in SKUS:
            required = total_required_by_doi[d][sku]
            planned = production_planned.get(sku, 0.0)
            gap = planned - required
            if not has_production_window:
                status = "N/A"
            elif required == 0:
                status = "ON TRACK"
            elif gap >= 0:
                status = "ON TRACK"
            else:
                status = "SHORTFALL"
            production_rows_by_doi[d].append({"sku": sku, "required": required, "planned": planned,
                                              "gap": gap, "status": status})

    return {"rows_": rows_, "wh_rows": wh_rows, "production_rows_by_doi": production_rows_by_doi,
            "has_production_window": has_production_window}


def get_pinned_date(supabase_url, key):
    r = requests.get(f"{supabase_url}/rest/v1/sop_dispatch_pinned_date", headers={
        "apikey": key, "Authorization": f"Bearer {key}"}, params={"select": "pinned_date", "id": "eq.1"},
        timeout=30)
    if not r.ok:
        sys.exit(f"Reading sop_dispatch_pinned_date failed ({r.status_code}): {r.text[:500]}")
    rows = r.json()
    return rows[0]["pinned_date"] if rows else None


def main():
    supabase_url, supabase_key = supabase_config()
    token = get_access_token()

    inv_rows = get_values(token, WH_CHANNEL_SKU_ID, "'Current Inventory'")
    rdh_rows = get_values(token, WH_CHANNEL_SKU_ID, "'Raw data helper'")
    day_wise_rows = get_values(token, WH_CHANNEL_SKU_ID, "'Day wise trackr'")
    uc_rows = get_values(token, WH_CHANNEL_SKU_ID, "'UC sales trackr'")
    az_rows = get_values(token, WH_CHANNEL_SKU_ID, "'Az - Daily trackr'")
    fk_rows = get_values(token, WH_CHANNEL_SKU_ID, "'FK - Daily trackr'")
    mt_rows = get_values(token, WH_CHANNEL_SKU_ID, "'MT - Daily trackr'")

    if not inv_rows or not rdh_rows:
        sys.exit("Fetched zero rows from Current Inventory or Raw data helper -- aborting without writing.")

    today = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Kolkata")).date()
    today_ymd = today.isoformat()

    current_inv, current_inv_by_city_uc = parse_current_inventory_by_channel(inv_rows)
    in_transit_network, in_transit_by_city_uc = parse_in_transit_from_current_inventory(inv_rows)
    po_records = parse_raw_data_helper(rdh_rows)
    actual_production_full = parse_daily_production(day_wise_rows)
    actual_prod_by_date = {ymd: v.get("COMBINED", {}) for ymd, v in actual_production_full.items()}

    channel_series = {
        "UC App + PLS": parse_daily_trackr_tab(uc_rows, 0, 1),
        "Amazon": parse_daily_trackr_tab(az_rows, 0, 23),
        "Flipkart": parse_daily_trackr_tab(fk_rows, 0, 23),
        "MT": parse_daily_trackr_tab(mt_rows, 0, 23),
    }
    planned_inward = {
        "Amazon": parse_daily_trackr_tab(az_rows, 0, 9)["series"],
        "Flipkart": parse_daily_trackr_tab(fk_rows, 0, 9)["series"],
        "MT": parse_daily_trackr_tab(mt_rows, 0, 9)["series"],
    }

    pinned_ymd = get_pinned_date(supabase_url, supabase_key)
    fixed_closing_targets = {}
    if pinned_ymd:
        diwali_rows = get_values(token, WH_CHANNEL_SKU_ID, "'Diwali Sales Plan - Overall '")
        fixed_closing_targets[pinned_ymd] = parse_diwali_opening_ask(diwali_rows, pinned_ymd)
    else:
        print("WARNING: sop_dispatch_pinned_date has no row -- skipping the pinned view this run "
              "(set it via SQL to enable).", file=sys.stderr)

    ctx = {
        "today_ymd": today_ymd, "current_inv": current_inv, "current_inv_by_city_uc": current_inv_by_city_uc,
        "in_transit_network": in_transit_network, "in_transit_by_city_uc": in_transit_by_city_uc,
        "po_records": po_records, "actual_prod_by_date": actual_prod_by_date, "channel_series": channel_series,
        "planned_inward": planned_inward, "fixed_closing_targets": fixed_closing_targets,
    }

    run_date = today_ymd
    plan_rows, prod_check_rows = [], []

    def emit(view_key, result):
        for r in result["rows_"]:
            for d in DOI_TARGETS:
                doi = r["doi"][d]
                doi_flag = doi["target"] if isinstance(doi["target"], (int, float)) else None
                plan_rows.append({
                    "run_date": run_date, "view_key": view_key, "scope_type": "CHANNEL", "scope": r["channel"],
                    "sku": r["sku"], "doi_target": d, "on_hand": r["on_hand"], "po_out": r["po_in"],
                    "sales_expected": r["sales_expected"], "projected_closing": r["projected_closing"],
                    "target_closing": doi["target"], "required_dispatch": doi["required_dispatch"],
                    "status": doi["status"],
                    "projected_doi": r["projected_doi"] if isinstance(r["projected_doi"], (int, float)) else None,
                    "projected_doi_flag": r["projected_doi"] if r["projected_doi"] == "INSUFFICIENT_DATA" else None,
                })
        for r in result["wh_rows"]:
            for d in DOI_TARGETS:
                doi = r["doi"][d]
                plan_rows.append({
                    "run_date": run_date, "view_key": view_key, "scope_type": "WAREHOUSE", "scope": r["warehouse"],
                    "sku": r["sku"], "doi_target": d, "on_hand": r["on_hand"], "po_out": r["po_out"],
                    "sales_expected": r["sales_expected"], "projected_closing": r["projected_closing"],
                    "target_closing": doi["target"], "required_dispatch": doi["required_dispatch"],
                    "status": doi["status"],
                    "projected_doi": r["projected_doi"] if isinstance(r["projected_doi"], (int, float)) else None,
                    "projected_doi_flag": r["projected_doi"] if r["projected_doi"] == "INSUFFICIENT_DATA" else None,
                })
        for d in DOI_TARGETS:
            for r in result["production_rows_by_doi"][d]:
                prod_check_rows.append({
                    "run_date": run_date, "view_key": view_key, "sku": r["sku"],
                    "production_planned": r["planned"], "required": r["required"], "gap": r["gap"],
                    "status": r["status"],
                })

    for n in HORIZON_DAYS:
        target_ymd = add_days_ymd(today_ymd, n)
        result = compute_for_date(ctx, target_ymd, n)
        emit(f"+{n}", result)

    if pinned_ymd:
        days_out = (datetime.date.fromisoformat(pinned_ymd) - today).days
        if days_out > 0:
            production_window_end_override = add_days_ymd(pinned_ymd, -1)
            result = compute_for_date(ctx, pinned_ymd, days_out, apply_fixed_targets=True,
                                       production_window_end_override=production_window_end_override)
            emit("PINNED", result)
        else:
            print(f"NOTE: pinned date {pinned_ymd} has already passed -- skipping the pinned view this run.",
                  file=sys.stderr)

    replace_by_filter(supabase_url, supabase_key, "sop_dispatch_plan", plan_rows,
                       {"run_date": f"eq.{run_date}"}, allow_empty=True)
    replace_by_filter(supabase_url, supabase_key, "sop_dispatch_production_check", prod_check_rows,
                       {"run_date": f"eq.{run_date}"}, allow_empty=True)

    print(f"Synced {len(plan_rows)} dispatch-plan row(s) and {len(prod_check_rows)} production-check "
          f"row(s) across {len(HORIZON_DAYS) + (1 if pinned_ymd else 0)} view(s) for run_date {run_date}.")


if __name__ == "__main__":
    main()
