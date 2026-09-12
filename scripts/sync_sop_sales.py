#!/usr/bin/env python3
"""
S&OP: Sales: Plan vs Actual + Day-on-Day Sales tabs.

Pulls WH-Channel-SKU's "Dashboard" tab ("Sale plan" + "Unit sold - Channel
Split" blocks -> current-month channel x SKU projection, derived the same
way parse_sop_master.js's computeProjection() does: each channel's monthly
total is turned into a share of the 4-channel total for that month, then
that share is applied to the SKU's monthly total) and the "actual sales"
tab (Q:S block -> current-month channel x SKU actuals; 6 further Date-
headed blocks -> the Day-on-Day Sales series), and upserts both into
Supabase. No Jarvis/Redash calls -- the "actual sales" tab is already kept
fresh by a separate, existing actual-sales-sync job; this script only
ever reads sheets.

IMPORTANT, discovered while building this: the live "Dashboard" tab's
block labels ("Sale plan" etc) now sit one column to the right of what
parse_sop_master.js assumes (that script hardcodes rows[i][0]; the sheet
has drifted to column B) -- which means the CURRENT PRODUCTION /sop-master
command's Sales tab is almost certainly silently broken today. This
script does NOT hardcode a column index for the label -- it scans each
row for its first non-blank cell -- specifically so a future column shift
doesn't silently break this tab too.

The "actual sales" tab also has two structurally IDENTICAL "Date + 6 SKU
columns" blocks (network-wide-by-SKU, and UC App+PLS-only-by-SKU) with no
distinguishing header -- see classify_date_block()'s docstring for how
this script tells them apart (by position, same limitation the sheet's
own anchor-cell design already has).

Credentials: GOOGLE_SERVICE_ACCOUNT_JSON, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY (all already provisioned, no new secrets).
"""
import datetime
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import (  # noqa: E402
    SKUS, WH_CHANNEL_SKU_ID, get_access_token, get_values, normalize_sku, pad_row,
    supabase_config, to_num, upsert,
)

DASH_CHANNELS = ["UC App+PLS", "Amazon", "Flipkart", "MT"]
DASH_CHANNEL_LABEL_MAP = {
    "UC APP + PLS": "UC App+PLS",
    "AMAZON": "Amazon",
    "FLIPKART DIRECT": "Flipkart",
    "MT": "MT",
}
# Query 554462's channel buckets (now read straight from the sheet's own Q:S block, not Jarvis) --
# same 4 channels as the projection, plus Others (footnote only, no Dashboard-tab counterpart).
ACTUALS_CHANNEL_MAP = {
    "UC APP + PLS": "UC App+PLS",
    "AMAZON": "Amazon",
    "FLIPKART": "Flipkart",
    "MT": "MT",
    "OTHERS": "Others",
}
ALL_ACTUALS_CHANNELS = DASH_CHANNELS + ["Others"]

MONTH_NAME_MAP = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                   "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def first_label(row):
    """The Dashboard tab's block labels used to sit in column A; they now sit one column over.
    Scanning for the first non-blank cell (rather than hardcoding an index) survives that kind of
    drift without needing another manual fix later."""
    for c in row:
        if c is not None and str(c).strip():
            return str(c).strip()
    return ""


def parse_month_header_cols(row):
    """Cells matching 'Mmm YY'/'Mmm YYYY' -> [{col, month_key: 'YYYY-MM'}]. Non-month columns in the
    same header row (e.g. "FY25 exit number", "FY 2026 - Total") simply don't match and are skipped."""
    cols = []
    for c, cell in enumerate(row):
        if cell is None:
            continue
        m = re.match(r"^([A-Za-z]{3})[a-z]*\s+(\d{2,4})$", str(cell).strip())
        if m and m.group(1).lower() in MONTH_NAME_MAP:
            yy = m.group(2)
            yyyy = yy if len(yy) == 4 else "20" + yy
            cols.append({"col": c, "month_key": f"{yyyy}-{MONTH_NAME_MAP[m.group(1).lower()]:02d}"})
    return cols


def parse_sale_plan_dashboard(rows):
    """Port of parse_sop_master.js's parseSalePlanDashboard(). Returns (sku_monthly, channel_monthly)."""
    sale_plan_row, channel_split_row = -1, -1
    for i, row in enumerate(rows):
        label = first_label(row).lower()
        if label == "sale plan":
            sale_plan_row = i
        if label == "unit sold - channel split":
            channel_split_row = i
        if sale_plan_row != -1 and channel_split_row != -1:
            break
    if sale_plan_row == -1:
        print('WARNING: could not find "Sale plan" block on Dashboard tab', file=sys.stderr)
    if channel_split_row == -1:
        print('WARNING: could not find "Unit sold - Channel Split" block on Dashboard tab', file=sys.stderr)

    sku_monthly = {}
    if sale_plan_row != -1:
        month_cols = parse_month_header_cols(rows[sale_plan_row])
        for i in range(sale_plan_row + 1, len(rows)):
            label = first_label(rows[i])
            if not label:
                break
            sku = normalize_sku(label)
            if not sku:
                continue
            sku_monthly.setdefault(sku, {})
            for mc in month_cols:
                row = pad_row(rows[i], mc["col"] + 1)
                sku_monthly[sku][mc["month_key"]] = to_num(row[mc["col"]])

    channel_monthly = {}
    if channel_split_row != -1:
        month_cols = parse_month_header_cols(rows[channel_split_row])
        for i in range(channel_split_row + 1, len(rows)):
            label = first_label(rows[i])
            if not label:
                break
            channel = DASH_CHANNEL_LABEL_MAP.get(label.upper())
            if not channel:
                continue
            channel_monthly.setdefault(channel, {})
            for mc in month_cols:
                row = pad_row(rows[i], mc["col"] + 1)
                channel_monthly[channel][mc["month_key"]] = to_num(row[mc["col"]])

    return sku_monthly, channel_monthly


def compute_projection(sku_monthly, channel_monthly, month_key):
    """Port of computeProjection(): channelShare = channelMonthly[ch][month] / sum(4 channels that
    month); projection[ch][sku] = skuMonthTotal[sku][month] * channelShare[ch]. Not a direct cell --
    a deliberate blend, matching the existing command exactly."""
    channel_total = sum((channel_monthly.get(ch, {}) or {}).get(month_key, 0) for ch in DASH_CHANNELS)
    result = {ch: {} for ch in DASH_CHANNELS}
    for sku in SKUS:
        sku_month_total = (sku_monthly.get(sku, {}) or {}).get(month_key, 0)
        for ch in DASH_CHANNELS:
            share = ((channel_monthly.get(ch, {}) or {}).get(month_key, 0) / channel_total) if channel_total > 0 else 0
            result[ch][sku] = sku_month_total * share
    return result


def parse_actual_sales_current_month(rows):
    """Port of parseActualSales(): the "actual sales" tab's Q:S block ('Sale Channel'/'SKU Name'/
    'net_orders'), a long/row-format table with no date column -- it's already scoped to the current
    month by whatever refreshes it (actual-sales-sync's own query rewrite), so no date filtering is
    done here. Returns {channel: {sku: qty}} across DASH_CHANNELS + 'Others'."""
    header = [str(c or "").strip().lower() for c in (rows[0] if rows else [])]

    def col_index(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    ch_col = col_index("sale channel")
    sku_col = col_index("sku name")
    qty_col = col_index("net_orders", "net orders")
    result = {ch: {s: 0.0 for s in SKUS} for ch in ALL_ACTUALS_CHANNELS}
    if ch_col is None or sku_col is None or qty_col is None:
        print('WARNING: could not find Sale Channel/SKU Name/net_orders header in actual sales '
              'tab\'s Q:S block', file=sys.stderr)
        return result
    for row in rows[1:]:
        row = pad_row(row, max(ch_col, sku_col, qty_col) + 1)
        raw_channel = str(row[ch_col] or "").strip()
        sku = normalize_sku(row[sku_col])
        if not sku:
            continue
        bucket = ACTUALS_CHANNEL_MAP.get(raw_channel.upper())
        if not bucket:
            continue
        result[bucket][sku] += to_num(row[qty_col])
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

    dashboard_rows = get_values(token, WH_CHANNEL_SKU_ID, "'Dashboard'")
    actual_sales_rows = get_values(token, WH_CHANNEL_SKU_ID, "'actual sales'")

    sku_monthly, channel_monthly = parse_sale_plan_dashboard(dashboard_rows)
    today = datetime.date.today()
    month_key = f"{today.year}-{today.month:02d}"
    month_start = f"{month_key}-01"
    projection = compute_projection(sku_monthly, channel_monthly, month_key)
    actuals = parse_actual_sales_current_month(actual_sales_rows)

    plan_actual_rows = []
    for ch in ALL_ACTUALS_CHANNELS:
        for sku in SKUS:
            proj = projection.get(ch, {}).get(sku, 0.0) if ch != "Others" else 0.0
            act = actuals.get(ch, {}).get(sku, 0.0)
            plan_actual_rows.append({
                "month_start": month_start, "channel": ch, "sku": sku,
                "projection": proj, "actual": act,
            })

    daily_rows = parse_daily_sales_blocks(actual_sales_rows)

    upsert(supabase_url, supabase_key, "sop_sales_plan_actual", plan_actual_rows, "month_start,channel,sku")
    upsert(supabase_url, supabase_key, "sop_daily_sales", daily_rows, "sale_date,series,dim")

    print(f"Synced {len(plan_actual_rows)} sales plan/actual row(s) for {month_key} and "
          f"{len(daily_rows)} daily sales row(s).")


if __name__ == "__main__":
    main()
