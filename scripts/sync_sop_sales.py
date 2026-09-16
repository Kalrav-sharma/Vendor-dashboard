#!/usr/bin/env python3
"""
S&OP: Sales: Plan vs Actual + Day-on-Day Sales tabs.

Projection (for Sales: Plan vs Actual) is each channel's own "Expected
Sale" daily-trackr block -- 'UC sales trackr' (UC App+PLS), 'Az - Daily
trackr' (Amazon), 'FK - Daily trackr' (Flipkart), 'MT - Daily trackr' (MT)
-- summed over the whole current month. Same source and parsing engine
(parse_daily_trackr_tab, in sop_common.py) that sync_sop_dispatch_plan.py
already uses for its own sales-expected term -- verified live to be the
correct column offsets (dayColStart=1 for UC, 23 for Az/FK/MT). This
replaced an earlier version that derived projection from the Dashboard
tab's monthly "Sale plan" blended by channel share -- per Anish, the
daily-trackr numbers are the ones that should drive this table.

Actuals (for both tabs) come from the "actual sales" tab's Date-headed
blocks: parse_daily_sales_blocks() turns them into the per-date
sop_daily_sales series, and Sales: Plan vs Actual's month-to-date actual is
then summed from those same rows (build_actuals_for_month) so the two tabs
-- and the DRR figures sync_sop_inventory.py derives from the same series
-- can never disagree. Note the asymmetry, which is deliberate per Anish:
projection is the FULL month's plan, actual is month-to-date. No
Jarvis/Redash calls -- the "actual sales" tab is already kept fresh by a
separate, existing actual-sales-sync job; this script only ever reads
sheets.

The "actual sales" tab also has two structurally IDENTICAL "Date + 6 SKU
columns" blocks (network-wide-by-SKU, and UC App+PLS-only-by-SKU) with no
distinguishing header -- see classify_date_block()'s docstring for how
this script tells them apart (by position, same limitation the sheet's
own anchor-cell design already has).

Credentials: GOOGLE_SERVICE_ACCOUNT_JSON, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY (all already provisioned, no new secrets).
"""
import calendar
import datetime
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import (  # noqa: E402
    SKUS, WH_CHANNEL_SKU_ID, get_access_token, get_values, normalize_sku,
    parse_daily_trackr_tab, replace_by_filter, supabase_config, to_num, upsert,
)

DASH_CHANNELS = ["UC App+PLS", "Amazon", "Flipkart", "MT"]
# Which daily-trackr tab + "Expected Sale" column offset feeds each channel's projection --
# verified live against the actual sheet (same offsets sync_sop_dispatch_plan.py's own
# sales-expected term uses): UC sales trackr's Expected Sale block starts at col 1 (no separate
# "Day" column); Az/FK/MT - Daily trackr's Expected Sale block starts at col 23.
PROJECTION_TAB_CONFIG = {
    "UC App+PLS": ("'UC sales trackr'", 1),
    "Amazon": ("'Az - Daily trackr'", 23),
    "Flipkart": ("'FK - Daily trackr'", 23),
    "MT": ("'MT - Daily trackr'", 23),
}
ALL_ACTUALS_CHANNELS = DASH_CHANNELS + ["Others"]
# Which per-SKU daily series (see parse_daily_sales_blocks) is which channel's actual sales.
# "Others" has no per-SKU block anywhere in the sheet -- only the channel-totals block carries it --
# so it's summed from there and written as a single sku='ALL' row (see build_actuals_for_month).
DAILY_SERIES_TO_CHANNEL = {
    "by_sku_uc": "UC App+PLS",
    "by_sku_amazon": "Amazon",
    "by_sku_flipkart": "Flipkart",
    "by_sku_mt": "MT",
}
OTHERS_SKU_SENTINEL = "ALL"


def compute_projection(token, month_start_ymd, month_end_ymd):
    """Sums each channel's own 'Expected Sale' daily-trackr series over the full current month
    (month_start_ymd..month_end_ymd inclusive) -- a direct read of each day's own real forecast
    number, no blending across channels or months. A date missing from a channel's own tab
    contributes 0, same convention used everywhere else in this codebase."""
    result = {ch: {s: 0.0 for s in SKUS} for ch in DASH_CHANNELS}
    for ch, (tab_name, day_col_start) in PROJECTION_TAB_CONFIG.items():
        rows = get_values(token, WH_CHANNEL_SKU_ID, tab_name)
        series = parse_daily_trackr_tab(rows, 0, day_col_start)["series"]
        d = month_start_ymd
        while d <= month_end_ymd:
            entry = series.get(d)
            if entry:
                for sku in SKUS:
                    result[ch][sku] += entry.get(sku, 0.0)
            d = (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat()
    return result


def build_actuals_for_month(daily_rows, month_start_ymd, month_end_ymd):
    """Month-to-date actuals per channel x SKU, summed from the very same per-date series this
    script already emits into sop_daily_sales -- so Sales: Plan vs Actual and Day-on-Day Sales can
    never disagree, and both agree with the DRR figures (sync_sop_inventory.py reads the same
    series).

    This replaced a parser that read the tab's 'Sale Channel'/'SKU Name'/'net_orders' long block and
    summed EVERY row in it with no date predicate whatsoever -- its docstring claimed the block was
    "already scoped to the current month by whatever refreshes it", which turned out to be false.
    Live on 2026-09-16 that overstated every channel: Amazon 9,211 vs a true Sept-to-date 2,751
    (3.3x), Flipkart 7,166 vs 1,333 (5.4x), UC App+PLS 6,024 vs 2,650, MT 2,754 vs 1,038.

    Returns {channel: {sku: qty}}; 'Others' is keyed by OTHERS_SKU_SENTINEL instead of a real SKU
    because the sheet only carries it at channel-total granularity."""
    result = {ch: {s: 0.0 for s in SKUS} for ch in DASH_CHANNELS}
    result["Others"] = {OTHERS_SKU_SENTINEL: 0.0}
    for r in daily_rows:
        if not (month_start_ymd <= r["sale_date"] <= month_end_ymd):
            continue
        channel = DAILY_SERIES_TO_CHANNEL.get(r["series"])
        if channel and r["dim"] in SKUS:
            result[channel][r["dim"]] += r["qty"]
        elif r["series"] == "by_channel" and r["dim"] == "Others":
            result["Others"][OTHERS_SKU_SENTINEL] += r["qty"]
    return result


KNOWN_CHANNEL_HEADER_MAP = {
    "UC APP + PLS": "UC App+PLS", "MT": "MT", "AMAZON": "Amazon", "FLIPKART": "Flipkart", "OTHERS": "Others",
}


def classify_date_block(rows, header_row, col):
    """Looks at the column immediately after a 'Date' header cell to decide what kind of block this
    is: 'sku' (next cell normalizes to a SKU -- collects up to 6 consecutive SKU columns) or
    'channel' (next cell matches one of the 5 known channel names -- collects up to 5). Any other
    shape (e.g. the tab's 'Warehouse/Channel/SKU/Net Orders' long-format block) returns None and is
    skipped -- this is what tells that block apart from the real SKU/channel blocks without hardcoding
    a column position for it."""
    next_cell = header_row[col + 1] if col + 1 < len(header_row) else None
    if normalize_sku(next_cell):
        cols = []
        for c in range(col + 1, min(col + 7, len(header_row))):
            sku = normalize_sku(header_row[c])
            if sku:
                cols.append({"col": c, "dim": sku})
            else:
                break
        return {"kind": "sku", "cols": cols}
    if KNOWN_CHANNEL_HEADER_MAP.get(str(next_cell or "").strip().upper()):
        cols = []
        for c in range(col + 1, min(col + 6, len(header_row))):
            ch = KNOWN_CHANNEL_HEADER_MAP.get(str(header_row[c] if c < len(header_row) else "").strip().upper())
            if ch:
                cols.append({"col": c, "dim": ch})
            else:
                break
        return {"kind": "channel", "cols": cols}
    return None


def parse_daily_sales_blocks(rows):
    """Finds every 'Date'-headed block in the actual sales tab's row 1 and classifies each (see
    classify_date_block). The un-ambiguous blocks (channel totals; Amazon/Flipkart/MT SKU blocks,
    each followed by their own '<Channel> Total' column) are identified definitively. The two bare
    'Date + 6 SKU columns, no trailing Total' blocks are NOT distinguishable by content alone --
    exactly like the sheet's own anchor-cell design already relies on fixed positions for these two
    -- so this assigns them by left-to-right order: the first is the network-wide total (by_sku),
    the second is the UC App+PLS-only one (by_sku_uc). If a future sheet edit reorders these two
    blocks, this would misattribute -- there is no way to avoid that without a distinguishing header
    the sheet doesn't currently have.

    Returns a list of {sale_date, series, dim, qty} rows."""
    if not rows:
        return []
    header_row = rows[0]
    date_cols = [c for c, cell in enumerate(header_row) if str(cell or "").strip().lower() == "date"]

    blocks = []  # [{col, kind, cols}]
    for col in date_cols:
        classified = classify_date_block(rows, header_row, col)
        if classified:
            blocks.append({"date_col": col, **classified})

    sku_blocks = [b for b in blocks if b["kind"] == "sku"]
    channel_blocks = [b for b in blocks if b["kind"] == "channel"]

    # Distinguish Amazon/Flipkart/MT SKU blocks by their trailing "<Channel> Total" column;
    # whatever's left (bare, no Total column) is assigned by position: 1st -> by_sku, 2nd -> by_sku_uc.
    named_series = {}
    bare_blocks = []
    for b in sku_blocks:
        last_col = b["cols"][-1]["col"] + 1 if b["cols"] else b["date_col"] + 1
        trailer = str(header_row[last_col] if last_col < len(header_row) else "").strip().upper()
        if trailer == "AMAZON TOTAL":
            named_series[id(b)] = "by_sku_amazon"
        elif trailer == "FLIPKART TOTAL":
            named_series[id(b)] = "by_sku_flipkart"
        elif trailer == "MT TOTAL":
            named_series[id(b)] = "by_sku_mt"
        else:
            bare_blocks.append(b)
    bare_blocks.sort(key=lambda b: b["date_col"])
    bare_series = ["by_sku", "by_sku_uc"]
    for b, series in zip(bare_blocks, bare_series):
        named_series[id(b)] = series

    out = []
    for b in sku_blocks + channel_blocks:
        series = named_series.get(id(b), "by_channel" if b["kind"] == "channel" else None)
        if not series:
            continue
        for i in range(1, len(rows)):
            row = rows[i]
            ymd = normalize_actual_sales_date(row[b["date_col"]] if b["date_col"] < len(row) else None)
            if not ymd:
                continue
            for c in b["cols"]:
                if c["col"] < len(row):
                    out.append({"sale_date": ymd, "series": series, "dim": c["dim"], "qty": to_num(row[c["col"]])})
    return out


def normalize_actual_sales_date(v):
    """The 'actual sales' tab's date cells are 'DD/MM/YY HH:MM' text (FORMATTED_VALUE rendering) --
    parsed explicitly here (never handed to a generic date parser) specifically to avoid the
    documented day<=12 dd/mm-vs-mm/dd misparse bug."""
    if not v:
        return None
    m = re.match(r"^(\d{2})/(\d{2})/(\d{2,4})", str(v).strip())
    if not m:
        return None
    yy = m.group(3)
    yyyy = yy if len(yy) == 4 else "20" + yy
    return f"{yyyy}-{m.group(2)}-{m.group(1)}"


def main():
    supabase_url, supabase_key = supabase_config()
    token = get_access_token()

    actual_sales_rows = get_values(token, WH_CHANNEL_SKU_ID, "'actual sales'")

    today = datetime.date.today()
    month_key = f"{today.year}-{today.month:02d}"
    month_start = f"{month_key}-01"
    month_end = f"{month_key}-{calendar.monthrange(today.year, today.month)[1]:02d}"
    projection = compute_projection(token, month_start, month_end)

    # Actuals are derived from the daily series, so these must be parsed first.
    daily_rows = parse_daily_sales_blocks(actual_sales_rows)
    actuals = build_actuals_for_month(daily_rows, month_start, today.isoformat())

    # Projection is the FULL month's plan (all of September), actual is month-to-date -- Anish's
    # explicit choice: the gap should read "how much of the month's plan is still to sell", not a
    # like-for-like pace comparison. The frontend labels both columns accordingly.
    plan_actual_rows = []
    for ch in DASH_CHANNELS:
        for sku in SKUS:
            plan_actual_rows.append({
                "month_start": month_start, "channel": ch, "sku": sku,
                "projection": projection.get(ch, {}).get(sku, 0.0),
                "actual": actuals.get(ch, {}).get(sku, 0.0),
            })
    plan_actual_rows.append({
        "month_start": month_start, "channel": "Others", "sku": OTHERS_SKU_SENTINEL,
        "projection": 0.0, "actual": actuals["Others"][OTHERS_SKU_SENTINEL],
    })

    # Wholesale-replace THIS month's rows rather than upsert them: the Others channel changed shape
    # (6 per-SKU rows -> one sku='ALL' row) on 2026-09-16, and an upsert would leave the 6 stale
    # per-SKU Others rows behind forever, which the frontend's Others footnote would then add on top
    # of the new row. Scoped to month_start, so prior months' history is untouched.
    replace_by_filter(supabase_url, supabase_key, "sop_sales_plan_actual", plan_actual_rows,
                       {"month_start": f"eq.{month_start}"})
    # sop_daily_sales stays an upsert -- it accumulates every month's history, never replaced.
    upsert(supabase_url, supabase_key, "sop_daily_sales", daily_rows, "sale_date,series,dim")

    print(f"Synced {len(plan_actual_rows)} sales plan/actual row(s) for {month_key} and "
          f"{len(daily_rows)} daily sales row(s).")


if __name__ == "__main__":
    main()
