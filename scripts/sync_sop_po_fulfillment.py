#!/usr/bin/env python3
"""
S&OP: PO Fulfillment tab.

Direct port of ~/.claude/scripts/parse_po_fulfillment.js's rolling 10-day
PO fulfillment simulation. Reads WH-Channel-SKU's "Current Inventory" tab
(on-hand + in-transit) and Copy Daily Input Anish's "Raw Data Sheet"
(dispatch records + channel POs), simulates day-by-day fulfillment status
for every individual PO row, groups RESCHEDULE/PARTIAL rows by SKU, and
computes a production-shortfall RCA against Phase B's
production_plan_snapshots table. Writes a wholesale-replace-per-run_date
snapshot to Supabase (sop_po_fulfillment_daily / sop_po_action_items /
sop_po_shortfall_rca) -- no LLM narrative generation anywhere; every
"RCA"/detail string here is template-formatted from already-computed
structured data, exactly like the source script.

Column indices for Raw Data Sheet are HARDCODED, not header-label-driven
-- deliberately, matching the source script exactly. This tab's own
header labels are known to be STALE/WRONG relative to the actual data
(e.g. the header says col 36 is "SO Number", but the real SO number
lives in col 37, itself mislabeled "PO/ Gate Pass Number" -- verified
live and matching the source script's own dated comments below). Scanning
for header text here would silently read the wrong column; the hardcoded
indices are the ground truth, manually verified against live data by
the source script's authors across two dated column-shift incidents.

Credentials: GOOGLE_SERVICE_ACCOUNT_JSON, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY (all already provisioned, no new secrets).
Depends on Phase B's production_plan_snapshots table (shortfall RCA).
"""
import datetime
import sys
import zoneinfo

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import (  # noqa: E402
    COPY_DAILY_INPUT_ANISH_ID, SKUS, WH_CHANNEL_SKU_ID, get_access_token, get_values, normalize_sku,
    pad_row, replace_by_filter, supabase_config, to_num,
)
from sync_sop_production import parse_daily_production  # noqa: E402

SAFETY_BUFFER = 50  # applied to every SKU except M0
WINDOW_DAYS = 10  # today + 9 more days
TARGET_CHANNELS = {"Amazon", "Primarc", "Flipkart", "Vijay Sales", "Croma", "Reliance"}
WH_ORDER = ["Bangalore", "Gurgaon", "Hyderabad", "Mumbai", "Kolkata"]

TRANSIT_ARRIVAL_DAY = {
    "AMBER": {"Gurgaon": 1, "Kolkata": 4, "Bangalore": 5, "Hyderabad": 5, "Mumbai": 5},
    "RONCH": {"Mumbai": 1, "Hyderabad": 2, "Bangalore": 3, "Gurgaon": 4, "Kolkata": 6},
}
MAX_TRANSIT_DAYS = {wh: max(TRANSIT_ARRIVAL_DAY["AMBER"][wh], TRANSIT_ARRIVAL_DAY["RONCH"][wh]) for wh in WH_ORDER}

WH_CODE_MAP = {
    "PB-UC-BLR": "Bangalore", "PB-UC-BOMBAY": "Mumbai", "PB-UC-MUM": "Mumbai",
    "PB-UC-GGN": "Gurgaon", "PB-UC-GGN-PATAUDI": "Gurgaon", "PB-UC-GGN_PATAUDI": "Gurgaon",
    "PB-UC-HYD": "Hyderabad", "PB-UC-KOL": "Kolkata", "PB-UC-KOL-PANCHLA": "Kolkata",
    "PB-UC-KOL-PANCHALA": "Kolkata",
}
DISPATCH_DEST_MAP = {
    "PB-UC-BLR": "Bangalore", "PB-UC-BOMBAY": "Mumbai", "PB-UC-GGN": "Gurgaon",
    "PB-UC-GGN-PATAUDI": "Gurgaon", "PB-UC-HYD": "Hyderabad", "PB-UC-KOL": "Kolkata",
    "PB-UC-KOL-PANCHLA": "Kolkata", "PB-UC-KOL-PANCHALA": "Kolkata",
}
PRODUCTION_ORIGINS = {"AMBER", "RONCH", "RONCH DAMAN"}

# Raw Data Sheet column indices (0-based), Copy Daily Input Anish -- hardcoded, see module docstring.
RDH_DATE_COL = 1
RDH_ORIGIN_COL = 4
RDH_DEST_COL = 5
RDH_MOVTYPE_COL = 8
RDH_CHANNEL_COL = 12
RDH_SKU_COLS = {"M0": 18, "M1-2nd Gen": 19, "M2 Pro": 21, "M1 Pro": 22, "M3 Pro": 23, "M3": 24}
RDH_SO_NUM_COL = 37
RDH_PO_APPT_COL = 38

CURRENT_YEAR = datetime.date.today().year


def channel_label(channel):
    return "Amazon (Primarc)" if channel == "Primarc" else channel


def parse_rdh_date(raw):
    """Raw Data Sheet's date column is 'D-MMM' (no year, e.g. '29-Jul') -- same ambiguous-year
    situation as Day wise trackr, same fix (this run's own year as the fallback)."""
    from sop_common import normalize_date
    return normalize_date(raw, default_year=CURRENT_YEAR)


def parse_on_hand(rows):
    """Port of parse_po_fulfillment.js's parseOnHand(): UC App-RO on-hand by city, from WH-Channel-SKU's
    Current Inventory tab. Distinct from Phase A's channel-bucket parser -- this one filters to any
    channel label containing 'UC APP'/'UC-APP' (not the exact INVENTORY_CHANNEL_MAP bucket match) and
    excludes a literal 'Delhi' city row, matching the source script exactly."""
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
    if header_row == -1:
        print("WARNING: could not find on-hand header in Current Inventory", file=sys.stderr)
        return {}
    if city_col == -1:
        city_col = 1

    result = {}
    current_channel = ""
    for i in range(header_row + 1, len(rows)):
        row = rows[i]
        channel_val = str(pad_row(row, channel_col + 1)[channel_col] or "").strip()
        city_val = str(pad_row(row, city_col + 1)[city_col] or "").strip()
        if channel_val:
            current_channel = channel_val
        if not city_val:
            continue
        chan_upper = current_channel.upper()
        if "UC APP" not in chan_upper and "UC-APP" not in chan_upper:
            continue
        if city_val.lower() == "delhi":
            continue
        result[city_val] = {}
        for sku in SKUS:
            col = sku_col_map.get(sku)
            result[city_val][sku] = to_num(row[col]) if col is not None and col < len(row) else 0.0
    return result


def parse_in_transit(rows):
    """Port of parse_po_fulfillment.js's parseInTransit(): generic scan (no text-label search) for a
    header row containing a leftmost 'TO' column plus >=3 SKU columns (guards the M1AS
    ambiguous-column bug the same way Phase A's inventory parser does), within WH-Channel-SKU's
    Current Inventory tab. Stops at 'Grand Total'."""
    header_row_idx, to_col = -1, -1
    sku_col_map = {}
    for i, row in enumerate(rows):
        has_to, sku_count, row_to_col = False, 0, -1
        tmp_map = {}
        for c, cell in enumerate(row):
            s = str(cell or "").strip().upper()
            if s == "TO" and row_to_col == -1:
                has_to, row_to_col = True, c
            if s in ("M1AS", "M1 AS") and "M1-2nd Gen" not in tmp_map:
                tmp_map["M1-2nd Gen"] = c
                sku_count += 1
                continue
            sku = normalize_sku(s)
            if sku and sku not in tmp_map:
                tmp_map[sku] = c
                sku_count += 1
        if has_to and sku_count >= 3:
            header_row_idx, to_col = i, row_to_col
            sku_col_map.update(tmp_map)
            break
    if header_row_idx == -1:
        print("WARNING: could not find in-transit header in Current Inventory", file=sys.stderr)
        return {}

    result = {}
    for i in range(header_row_idx + 1, len(rows)):
        row = rows[i]
        to_val = str(pad_row(row, to_col + 1)[to_col] or "").strip().upper()
        if not to_val:
            continue
        if to_val == "GRAND TOTAL":
            break
        city = WH_CODE_MAP.get(to_val)
        if not city:
            continue
        result.setdefault(city, {s: 0.0 for s in SKUS})
        for sku in SKUS:
            col = sku_col_map.get(sku)
            if col is not None and col < len(row):
                result[city][sku] = result[city].get(sku, 0.0) + to_num(row[col])
    return result


def parse_dispatch_records(rows):
    """Port of parseDispatchRecords(): production->warehouse dispatch history from Raw Data Sheet,
    sorted most-recent-first per (warehouse, sku)."""
    dispatches = {wh: {s: [] for s in SKUS} for wh in WH_ORDER}
    for row in rows[1:]:
        row = pad_row(row, RDH_PO_APPT_COL + 1)
        origin_raw = str(row[RDH_ORIGIN_COL] or "").strip().upper()
        if origin_raw not in PRODUCTION_ORIGINS:
            continue
        dest_raw = str(row[RDH_DEST_COL] or "").strip().upper()
        wh = DISPATCH_DEST_MAP.get(dest_raw)
        if not wh:
            continue
        ymd = parse_rdh_date(row[RDH_DATE_COL])
        if not ymd:
            continue
        origin = "AMBER" if origin_raw == "AMBER" else "RONCH"
        for sku in SKUS:
            qty = to_num(row[RDH_SKU_COLS[sku]])
            if qty > 0:
                dispatches[wh][sku].append({"ymd": ymd, "qty": qty, "origin": origin})
    for wh in WH_ORDER:
        for sku in SKUS:
            dispatches[wh][sku].sort(key=lambda d: d["ymd"], reverse=True)
    return dispatches


def compute_in_transit_etas(in_transit, dispatches, today):
    """Port of computeInTransitETAs(): apportions each warehouse/SKU's current in-transit quantity to
    ETAs by walking historical dispatch batches backward (most recent first) until it's accounted
    for; conservative fallback ETA for any leftover with no matching dispatch record."""
    eta_map = {}
    for wh in WH_ORDER:
        eta_map[wh] = {}
        for sku in SKUS:
            needed = in_transit.get(wh, {}).get(sku, 0.0)
            if needed == 0:
                eta_map[wh][sku] = []
                continue
            tranches = []
            remaining = needed
            for batch in dispatches[wh][sku]:
                if remaining <= 0:
                    break
                taken = min(batch["qty"], remaining)
                raw_eta = datetime.date.fromisoformat(batch["ymd"]) + datetime.timedelta(
                    days=TRANSIT_ARRIVAL_DAY[batch["origin"]][wh])
                eta = max(raw_eta, today)
                tranches.append({"qty": taken, "eta": eta})
                remaining -= taken
            if remaining > 0:
                tranches.append({"qty": remaining, "eta": today + datetime.timedelta(days=MAX_TRANSIT_DAYS[wh])})
            tranches.sort(key=lambda t: t["eta"])
            merged = []
            for t in tranches:
                if merged and merged[-1]["eta"] == t["eta"]:
                    merged[-1]["qty"] += t["qty"]
                else:
                    merged.append(dict(t))
            eta_map[wh][sku] = merged
    return eta_map


def parse_pos(rows, today, end_date):
    """Port of parsePOs(): individual channel-PO rows within the window, grouped
    posByDay[day_offset][wh][channel][sku] = [{qty, confirmed, po_number}, ...]."""
    pos_by_day = {d: {} for d in range(WINDOW_DAYS)}
    for row in rows[1:]:
        row = pad_row(row, RDH_PO_APPT_COL + 1)
        ymd = parse_rdh_date(row[RDH_DATE_COL])
        if not ymd:
            continue
        date_val = datetime.date.fromisoformat(ymd)
        if date_val < today or date_val > end_date:
            continue
        if str(row[RDH_MOVTYPE_COL] or "").strip() != "MM":
            continue
        channel = str(row[RDH_CHANNEL_COL] or "").strip()
        if channel not in TARGET_CHANNELS:
            continue
        origin_key = str(row[4] or "").strip().upper()  # RDH_ORIGIN_COL, same index as dispatch parser
        wh = WH_CODE_MAP.get(origin_key)
        if not wh:
            continue
        day_offset = (date_val - today).days
        if day_offset < 0 or day_offset >= WINDOW_DAYS:
            continue

        confirmed = str(row[RDH_SO_NUM_COL] or "").strip() != ""
        po_number = str(row[RDH_PO_APPT_COL] or "").strip()
        pos_by_day[day_offset].setdefault(wh, {}).setdefault(channel, {})
        for sku in SKUS:
            qty = to_num(row[RDH_SKU_COLS[sku]])
            if qty > 0:
                pos_by_day[day_offset][wh][channel].setdefault(sku, []).append(
                    {"qty": qty, "confirmed": confirmed, "po_number": po_number})
    return pos_by_day


def run_fulfillment_analysis(on_hand, eta_map, pos_by_day, today):
    """Port of runFulfillmentAnalysis(): day-by-day rolling simulation, day 0..WINDOW_DAYS-1."""
    available = {wh: {s: on_hand.get(wh, {}).get(s, 0.0) for s in SKUS} for wh in WH_ORDER}
    it_released_idx = {wh: {s: 0 for s in SKUS} for wh in WH_ORDER}
    it_committed = {wh: {s: 0.0 for s in SKUS} for wh in WH_ORDER}
    results = []

    for day_offset in range(WINDOW_DAYS):
        date = today + datetime.timedelta(days=day_offset)
        same_day_release = {wh: {s: 0.0 for s in SKUS} for wh in WH_ORDER}

        for wh in WH_ORDER:
            for sku in SKUS:
                tranches = eta_map[wh][sku]
                idx = it_released_idx[wh][sku]
                while idx < len(tranches) and tranches[idx]["eta"] <= date:
                    committed_from_this = min(it_committed[wh][sku], tranches[idx]["qty"])
                    net_release = tranches[idx]["qty"] - committed_from_this
                    available[wh][sku] += net_release
                    it_committed[wh][sku] -= committed_from_this
                    if tranches[idx]["eta"] == date:
                        same_day_release[wh][sku] += net_release
                    idx += 1
                it_released_idx[wh][sku] = idx

        day_pos = pos_by_day.get(day_offset, {})
        for wh in WH_ORDER:
            for channel in ["Amazon", "Primarc", "Flipkart", "Vijay Sales", "Croma", "Reliance"]:
                sku_map = day_pos.get(wh, {}).get(channel, {})
                for sku in SKUS:
                    po_entries = sku_map.get(sku)
                    if not po_entries:
                        continue
                    for po_entry in po_entries:
                        po_qty = po_entry["qty"]
                        if po_qty == 0:
                            continue
                        is_confirmed = po_entry["confirmed"]

                        tranches = eta_map[wh][sku]
                        unreleased = tranches[it_released_idx[wh][sku]:]
                        unreleased_total = sum(t["qty"] for t in unreleased)
                        full_it_qty = max(0.0, unreleased_total - it_committed[wh][sku])
                        it_eta = None
                        cum_committed = it_committed[wh][sku]
                        for t in unreleased:
                            if cum_committed < t["qty"]:
                                it_eta = t["eta"]
                                break
                            cum_committed -= t["qty"]
                        window_end = today + datetime.timedelta(days=WINDOW_DAYS - 1)
                        it_arrives_late = it_eta is None or it_eta > window_end
                        it_qty = 0.0 if it_arrives_late else full_it_qty

                        if is_confirmed:
                            status = "CONFIRMED"
                            detail = f"SO confirmed · {po_qty:g} already blocked (on-hand unaffected)"
                        else:
                            buffer = 0 if sku == "M0" else SAFETY_BUFFER
                            avail = max(0.0, available[wh][sku] - buffer)
                            same_day_contrib = same_day_release[wh][sku]
                            avail_without = max(0.0, available[wh][sku] - same_day_contrib - buffer)

                            if avail_without >= po_qty:
                                status = "FULFILL"
                                detail = f"{avail_without - po_qty:g} remaining after fulfillment"
                                available[wh][sku] -= po_qty
                            elif avail >= po_qty:
                                status = "TRANSIT-FULFILL"
                                detail = (f"Fulfillable when inventory arrives (ETA: {date.isoformat()}) · "
                                          f"{avail - po_qty:g} to spare after fulfillment")
                                available[wh][sku] -= po_qty
                            elif avail + it_qty >= po_qty:
                                from_it = po_qty - avail
                                it_eta_str = it_eta.isoformat() if it_eta else "(unknown)"
                                status = "RESCHEDULE"
                                detail = (f"Reschedule to {it_eta_str} — in-transit arrives then · "
                                          f"{avail:g} on-hand now, needs {from_it:g} more from in-transit")
                                available[wh][sku] = 0.0
                            elif avail > po_qty * 0.5:
                                shortfall = po_qty - avail
                                pct = round(avail / po_qty * 100)
                                status = "PARTIAL/NEEDS IN-TRANSIT"
                                it_note = (f" ({full_it_qty:g} in-transit arriving "
                                           f"{it_eta.isoformat() if it_eta else 'later'})") if full_it_qty > 0 else ""
                                detail = (f"Can be partially fulfilled: {avail:g} of {po_qty:g} units "
                                          f"({pct}%){it_note}; shortfall of {shortfall:g}")
                                available[wh][sku] = 0.0
                            else:
                                status = "RESCHEDULE"
                                if unreleased_total > 0:
                                    it_eta_str = it_eta.isoformat() if it_eta else \
                                        (today + datetime.timedelta(days=MAX_TRANSIT_DAYS[wh])).isoformat()
                                    total_after_it = avail + full_it_qty
                                    if total_after_it >= po_qty:
                                        detail = (f"{avail:g} on-hand only; {full_it_qty:g} in-transit "
                                                  f"(ETA: {it_eta_str}). Reschedule PO to {it_eta_str} to fulfill.")
                                    else:
                                        detail = (f"{avail:g} on-hand + {full_it_qty:g} in-transit "
                                                  f"(ETA: {it_eta_str}) = {total_after_it:g} total; still "
                                                  f"insufficient for PO of {po_qty:g}.")
                                else:
                                    detail = f"Only {avail:g} available; PO needs {po_qty:g}."
                                available[wh][sku] = 0.0

                        results.append({
                            "sim_date": date.isoformat(), "warehouse": wh, "channel": channel_label(channel),
                            "sku": sku, "po_qty": po_qty, "status": status, "detail": detail,
                            "po_number": po_entry["po_number"],
                        })
    return results


def compute_shortfall_rca(sku, today, snapshots_by_sku_date, actual_by_date):
    """Port of computeShortfallRCA(): trailing 14 days (today excluded), comparing Phase B's frozen
    production_plan_snapshots (COMBINED facility) against live Combined Actual Production. Three
    distinguishable outcomes -- never collapse (b)/(c)."""
    shortfalls = []
    compared_dates = 0
    for d in range(1, 15):
        ymd = (today - datetime.timedelta(days=d)).isoformat()
        planned = snapshots_by_sku_date.get(ymd, {}).get(sku)
        actual = actual_by_date.get(ymd, {}).get(sku)
        if planned is None or actual is None:
            continue
        compared_dates += 1
        delta = planned - actual
        if delta > 0:
            shortfalls.append({"date": ymd, "planned": planned, "actual": actual, "delta": delta})
    shortfalls.sort(key=lambda s: s["date"])
    if shortfalls:
        outcome = "SHORTFALL_DATES_FOUND"
        note = "Production shortfall: " + "; ".join(
            f"planned {s['planned']:g}, actual {s['actual']:g} on {s['date']} (Δ {s['delta']:g})"
            for s in shortfalls)
    elif compared_dates:
        outcome = "NO_SHORTFALL_LIKELY_PO_VOLUME"
        note = ("No production shortfall found in the trailing 14 days for this SKU — likely due to "
                "PO volume exceeding available supply.")
    else:
        outcome = "UNAVAILABLE"
        note = ("Snapshot data unavailable for the trailing 14-day window — cannot determine whether a "
                "production shortfall occurred.")
    return {"outcome": outcome, "detail_dates": shortfalls, "note": note}


def fetch_snapshots_by_sku_date(supabase_url, key):
    """Reads production_plan_snapshots (Phase B), facility=COMBINED only, paginated past PostgREST's
    1000-row cap. Returns {ymd: {sku: planned_qty}}."""
    import requests
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    out = {}
    offset = 0
    while True:
        r = requests.get(
            f"{supabase_url}/rest/v1/production_plan_snapshots",
            headers={**headers, "Range": f"{offset}-{offset + 999}"},
            params={"facility": "eq.COMBINED", "select": "snapshot_date,sku,planned_qty"},
            timeout=30,
        )
        if not r.ok:
            sys.exit(f"Reading production_plan_snapshots failed ({r.status_code}): {r.text[:500]}")
        rows = r.json()
        for row in rows:
            out.setdefault(row["snapshot_date"], {})[row["sku"]] = row["planned_qty"]
        if len(rows) < 1000:
            break
        offset += 1000
    return out


def main():
    supabase_url, supabase_key = supabase_config()
    token = get_access_token()

    inv_rows = get_values(token, WH_CHANNEL_SKU_ID, "'Current Inventory'")
    day_wise_rows = get_values(token, WH_CHANNEL_SKU_ID, "'Day wise trackr'")
    raw_data_rows = get_values(token, COPY_DAILY_INPUT_ANISH_ID, "'Raw Data Sheet'")

    # Guard the SHEET FETCH, not the business outcome -- a genuinely-empty 10-day PO window is a real,
    # legitimate state (the source command handles it gracefully too), but an empty raw_data_rows/
    # inv_rows almost certainly means a transient fetch failure, and writing zero fulfillment rows in
    # that case would silently wipe out a good prior run.
    if not raw_data_rows or not inv_rows:
        sys.exit("Fetched zero rows from Current Inventory or Raw Data Sheet -- aborting without "
                 "writing (a transient fetch failure looks the same as an empty sheet).")

    today = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Kolkata")).date()
    end_date = today + datetime.timedelta(days=WINDOW_DAYS - 1)

    on_hand = parse_on_hand(inv_rows)
    in_transit = parse_in_transit(inv_rows)
    dispatches = parse_dispatch_records(raw_data_rows)
    eta_map = compute_in_transit_etas(in_transit, dispatches, today)
    pos_by_day = parse_pos(raw_data_rows, today, end_date)
    results = run_fulfillment_analysis(on_hand, eta_map, pos_by_day, today)

    actual_production = parse_daily_production(day_wise_rows)
    actual_by_date = {ymd: v.get("COMBINED", {}) for ymd, v in actual_production.items()}
    snapshots_by_sku_date = fetch_snapshots_by_sku_date(supabase_url, supabase_key)

    run_date = today.isoformat()
    fulfillment_rows = [{"run_date": run_date, **r} for r in results]

    action_item_rows = []
    for bucket, status_filter in (("RESCHEDULE", "RESCHEDULE"), ("PARTIAL", "PARTIAL/NEEDS IN-TRANSIT")):
        by_sku = {}
        for r in results:
            if r["status"] == status_filter:
                by_sku.setdefault(r["sku"], []).append(r)
        for sku, rows in by_sku.items():
            total_qty = sum(r["po_qty"] for r in rows)
            note = "; ".join(f"{r['warehouse']}/{r['channel']} {r['po_qty']:g}u ({r['sim_date']}): {r['detail']}"
                              for r in rows)
            action_item_rows.append({"run_date": run_date, "bucket": bucket, "sku": sku,
                                      "total_qty": total_qty, "note": note[:4000]})

    rca_skus = {r["sku"] for r in results if r["status"] in ("RESCHEDULE", "PARTIAL/NEEDS IN-TRANSIT")}
    rca_rows = []
    for sku in rca_skus:
        rca = compute_shortfall_rca(sku, today, snapshots_by_sku_date, actual_by_date)
        rca_rows.append({"run_date": run_date, "sku": sku, "outcome": rca["outcome"],
                          "detail_dates": rca["detail_dates"], "note": rca["note"]})

    replace_by_filter(supabase_url, supabase_key, "sop_po_fulfillment_daily", fulfillment_rows,
                       {"run_date": f"eq.{run_date}"}, allow_empty=True)
    replace_by_filter(supabase_url, supabase_key, "sop_po_action_items", action_item_rows,
                       {"run_date": f"eq.{run_date}"}, allow_empty=True)
    replace_by_filter(supabase_url, supabase_key, "sop_po_shortfall_rca", rca_rows,
                       {"run_date": f"eq.{run_date}"}, allow_empty=True)

    print(f"Synced {len(fulfillment_rows)} PO-fulfillment row(s), {len(action_item_rows)} action-item "
          f"group(s), {len(rca_rows)} shortfall-RCA row(s) for run_date {run_date}.")


if __name__ == "__main__":
    main()
