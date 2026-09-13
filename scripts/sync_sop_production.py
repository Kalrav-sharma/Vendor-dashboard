#!/usr/bin/env python3
"""
S&OP: Production Plan tab, live view.

Pulls WH-Channel-SKU's "Day wise trackr" tab and upserts the Combined
Planned Production block plus the Combined/Ronch/Amber Actual Production
blocks into Supabase's sop_production_daily. Direct port of
parse_first_mile_dispatch_plan.js's parseDailyProduction() for the
facility split, plus parse_sop_master.js's parseDayWiseTrackrBlock()
pattern for the (network-wide-only) planned block.

Runs frequently (every 30-60 min, business hours) so the live view stays
current. This table's PAST-dated planned_qty values are NOT what the
frontend should trust for history -- a date's Actual Production cell
holds a placeholder plan value right up until that date happens, then
gets silently overwritten with the true actual (a real, documented sheet
behavior). See sync_sop_production_snapshot.py + production_plan_snapshots
for how the portal actually solves that, instead of just re-reading this
table's live planned_qty for a past date (which may have already flipped
to the actual by the time anyone looks).

Also runs a reconciliation check (Ronch + Amber vs Combined) and logs a
warning (does not fail the sync) if they diverge by more than 0.5 units --
per the source script's own comment, this split was left unmaintained
through Jun-Jul 2026, so it only reconciles from Aug-2026 onward. The
frontend surfaces this as a static "unreliable before Aug 2026" caveat
rather than trying to read this warning back out of a log.

Credentials: GOOGLE_SERVICE_ACCOUNT_JSON, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY (all already provisioned, no new secrets).
"""
import datetime
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import (  # noqa: E402
    SKUS, WH_CHANNEL_SKU_ID, get_access_token, get_values, normalize_date, normalize_sku,
    pad_row, supabase_config, to_num, upsert,
)

FACILITIES = ["COMBINED", "RONCH", "AMBER"]
# "Day wise trackr" mixes 'Jun 1 2026' (year included) and '1-Sep' (no year) date formats in the
# same column -- the year-less form needs a fallback year, which is simply this run's own year
# (the sheet only ever carries the current +/- a few months of data). See normalize_date()'s
# docstring for the (accepted) year-boundary caveat.
CURRENT_YEAR = datetime.date.today().year


def find_sku_cols(header_row, base_col):
    cols = []
    for c in range(base_col, min(base_col + 6, len(header_row))):
        sku = normalize_sku(header_row[c])
        if sku:
            cols.append({"col": c, "sku": sku})
        else:
            break
    return cols


def parse_daily_production(rows):
    """Port of parseDailyProduction(): finds 'Actual Production Ronch'/'Actual Production Amber'/
    'Combined Actual Production' header cells in the first ~10 rows, each followed by its own 6-SKU
    sub-header row, then reads every dated row beneath. Returns {ymd: {facility: {sku: qty}}}."""
    facility_col = {}
    block_header_row = -1
    for i in range(min(10, len(rows))):
        for c, cell in enumerate(rows[i]):
            label = str(cell or "").strip()
            if label == "Actual Production Ronch":
                facility_col["RONCH"] = c
                block_header_row = i
            if label == "Actual Production Amber":
                facility_col["AMBER"] = c
                block_header_row = i
            if label == "Combined Actual Production":
                facility_col["COMBINED"] = c
                block_header_row = i
        if "RONCH" in facility_col and "AMBER" in facility_col:
            break
    if "RONCH" not in facility_col or "AMBER" not in facility_col:
        print("WARNING: could not find Actual Production Ronch/Amber headers in Day wise trackr",
              file=sys.stderr)
        return {}

    sku_label_row = rows[block_header_row + 1]
    ronch_cols = find_sku_cols(sku_label_row, facility_col["RONCH"])
    amber_cols = find_sku_cols(sku_label_row, facility_col["AMBER"])
    combined_cols = find_sku_cols(sku_label_row, facility_col["COMBINED"]) if "COMBINED" in facility_col else []

    result = {}
    for i in range(block_header_row + 2, len(rows)):
        row = rows[i]
        ymd = normalize_date(row[0] if row else None, default_year=CURRENT_YEAR)
        if not ymd:
            continue
        entry = {"RONCH": {}, "AMBER": {}, "COMBINED": {}}
        for c in ronch_cols:
            entry["RONCH"][c["sku"]] = to_num(pad_row(row, c["col"] + 1)[c["col"]])
        for c in amber_cols:
            entry["AMBER"][c["sku"]] = to_num(pad_row(row, c["col"] + 1)[c["col"]])
        for c in combined_cols:
            entry["COMBINED"][c["sku"]] = to_num(pad_row(row, c["col"] + 1)[c["col"]])
        result[ymd] = entry
    return result


def parse_combined_planned_production(rows):
    """Port of parse_sop_master.js's parseDayWiseTrackrBlock('Planned Production') -- matches a
    literal 'planned production' cell OR 'combined planned production' (the live sheet's actual
    label, confirmed by reading it directly). Network-wide only -- no per-facility split exists for
    PLANNED production anywhere in the source sheet. Returns {ymd: {sku: qty}}."""
    block_header_row, block_col = -1, -1
    for i in range(min(10, len(rows))):
        row = [str(c or "").strip().lower() for c in rows[i]]
        for c, v in enumerate(row):
            if v in ("planned production", "combined planned production"):
                block_header_row, block_col = i, c
                break
        if block_header_row != -1:
            break
    if block_header_row == -1:
        print('WARNING: could not find "Planned Production" header in Day wise trackr', file=sys.stderr)
        return {}

    sku_label_row = rows[block_header_row + 1]
    cols = find_sku_cols(sku_label_row, block_col)

    result = {}
    for i in range(block_header_row + 2, len(rows)):
        row = rows[i]
        ymd = normalize_date(row[0] if row else None, default_year=CURRENT_YEAR)
        if not ymd:
            continue
        entry = {}
        for c in cols:
            entry[c["sku"]] = to_num(pad_row(row, c["col"] + 1)[c["col"]])
        result[ymd] = entry
    return result


def main():
    supabase_url, supabase_key = supabase_config()
    token = get_access_token()

    rows = get_values(token, WH_CHANNEL_SKU_ID, "'Day wise trackr'")
    actual_by_date = parse_daily_production(rows)
    planned_by_date = parse_combined_planned_production(rows)

    all_dates = sorted(set(actual_by_date) | set(planned_by_date))
    out_rows = []
    mismatches = []
    for ymd in all_dates:
        actual = actual_by_date.get(ymd, {"RONCH": {}, "AMBER": {}, "COMBINED": {}})
        planned_combined = planned_by_date.get(ymd, {})
        for sku in SKUS:
            combined_actual = actual["COMBINED"].get(sku, 0.0)
            ronch_actual = actual["RONCH"].get(sku, 0.0)
            amber_actual = actual["AMBER"].get(sku, 0.0)
            if abs((ronch_actual + amber_actual) - combined_actual) > 0.5 and ymd >= "2026-08-01":
                mismatches.append((ymd, sku, combined_actual, ronch_actual + amber_actual))
            out_rows.append({"prod_date": ymd, "sku": sku, "facility": "COMBINED",
                              "planned_qty": planned_combined.get(sku), "actual_qty": combined_actual})
            out_rows.append({"prod_date": ymd, "sku": sku, "facility": "RONCH",
                              "planned_qty": None, "actual_qty": ronch_actual})
            out_rows.append({"prod_date": ymd, "sku": sku, "facility": "AMBER",
                              "planned_qty": None, "actual_qty": amber_actual})

    if mismatches:
        print(f"WARNING: {len(mismatches)} PRODUCTION BLOCK MISMATCH(es) (Ronch+Amber vs Combined, "
              f"dates >= 2026-08-01):", file=sys.stderr)
        for ymd, sku, combined, split in mismatches[:12]:
            print(f"  {ymd} {sku}: combined={combined:.0f} vs split={split:.0f}", file=sys.stderr)

    upsert(supabase_url, supabase_key, "sop_production_daily", out_rows, "prod_date,sku,facility")
    print(f"Synced {len(out_rows)} production row(s) across {len(all_dates)} date(s) "
          f"({len(mismatches)} facility-split mismatches logged).")


if __name__ == "__main__":
    main()
