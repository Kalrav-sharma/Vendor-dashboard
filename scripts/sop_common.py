#!/usr/bin/env python3
"""
Shared helpers for every scripts/sync_sop_*.py script: Google service-account
auth against the Sheets API, Supabase PostgREST writes, and the SKU
normalization / date-parsing/number-parsing helpers every one of these
scripts needs against the same two source sheets (WH-Channel-SKU, Copy
Daily Input Anish).

Not a general-purpose library -- keep this scoped to what the S&OP sync
scripts actually share; sync_mm_rate_card.py and sync_to_supabase.py
(pre-existing, unrelated sheets) intentionally don't use this.
"""
import calendar
import os
import re
import sys
import tempfile

import google.auth.transport.requests
import requests
from google.oauth2 import service_account

WH_CHANNEL_SKU_ID = "17VKM18L9vlo72LtcKvTOmCbcWqJaRc3sSJIwhthbW8U"
COPY_DAILY_INPUT_ANISH_ID = "1e04j1Z77tp1dp-b45WSBE_fa-d_y38WlbHQPyeVOVzU"
REQUEST_TIMEOUT = 30

SKUS = ['M0', 'M1-2nd Gen', 'M1 Pro', 'M2 Pro', 'M3', 'M3 Pro']

# ---------------------------------------------------------------------------
# Uniware facility / SKU mappings.
#
# These live here (rather than in sync_uniware_inventory.py, which owns the HTTP
# side) because four scripts have to roll the same raw per-facility snapshot up
# the same way -- if any of them disagreed, the portal would show two different
# on-hand numbers for the same warehouse on different tabs, which is exactly the
# problem switching to Uniware is meant to end.
#
# Lifted from the already-proven ~/.claude/scripts/sync-warehouse-inventory-to-sheet.js,
# which has been pulling these same facilities and SKUs unattended for a while --
# same codes, same spellings, same city groupings.
# ---------------------------------------------------------------------------

# Uniware's Item SkuCode -> our SKU name. Case is NOT consistent across these
# (M0 is "UC/NATIVE/..." while the rest are "UC/Native/...") and the CSV matches
# verbatim, so never upper/lower-case these before comparing.
UNIWARE_SKU_MAP = {
    'UC/NATIVE/12501/M0': 'M0',
    'UC/Native/M1/12401': 'M1-2nd Gen',   # Uniware calls this one M1AS
    'UC/Native/M1Pro/12601': 'M1 Pro',
    'UC/Native/M2/12502': 'M2 Pro',
    'UC/Native/M3/12602': 'M3',
    'UC/Native/M3Pro/12603': 'M3 Pro',
}

# The 5 mother warehouses. Gurgaon and Kolkata are each the sum of two real
# Uniware facilities; the others are 1:1.
WAREHOUSE_FACILITY_CODES = {
    'Bangalore': ['PB-UC-BLR'],
    'Gurgaon': ['PB-UC-GGN', 'PB-UC-GGN-PATAUDI'],
    'Hyderabad': ['PB-UC-HYD'],
    'Mumbai': ['PB-UC-BOMBAY'],
    'Kolkata': ['PB-UC-KOL', 'PB-UC-KOL-PANCHLA'],
}

# Individual dark store -> the DTDC/SFX bucket it rolls into. Deliberately the
# stores the business already tracks, NOT every dark store Uniware exposes -- per
# Anish, so the totals stay comparable to what the portal has always shown. Order
# and bucket labels mirror the "Current Inventory" tab's own rows 37-63.
#
# Adding or dropping a store is a one-line change here and nothing else: the DRR
# block columns are discovered from the sheet by find_trackr_title_cols() rather
# than hardcoded, and both dark-store tables are written with replace_by_filter()
# rather than upsert, so a dropped store leaves no orphan row behind.
#
# 2026-09-22: PB-UC-BLR-SARAKKI dropped (store gone non-functional) and
# PB-UC-BLR-CHAMRAJPET added in its place; the 6 "SFX MFCs" facilities added as a
# new sixth bucket. 2026-09-25: PB-UC-DEL-JHILMIL and PB-UC-GGN-SOHNA dropped (not considered
# dark stores any more, per Anish), leaving 19 dark stores + 6 MFCs.
DARK_STORE_FACILITIES = {
    'PB-UC-BLR-NERALURU': 'DTDC Bangalore',
    'PB-UC-BLR-WHITEFIELD': 'DTDC Bangalore',
    'PB-UC-BLR-YELAHANKA': 'DTDC Bangalore',
    'PB-UC-BLR-BUMMANAHALLI': 'DTDC Bangalore',
    'PB-UC-BLR-CHAMRAJPET': 'DTDC Bangalore',
    'PB-UC-DEL-KAPASHERA': 'DTDC Gurgaon',
    'PB-UC-DEL-OKHLA': 'DTDC Gurgaon',
    'PB-UC-DEL-ROHINI': 'DTDC Gurgaon',
    'PB-UC-DEL-SHAHDARA': 'DTDC Gurgaon',
    'PB-UC-KOL-AGARPARA': 'DTDC Kolkata',
    'PB-UC-KOL-CAMACSTREET': 'DTDC Kolkata',
    'PB-UC-KOL-TARATALA': 'DTDC Kolkata',
    'PB-UC-KOL-RAJARHAT': 'DTDC Kolkata',
    'PB-UC-BOM-SION': 'SFX Mumbai',
    'PB-UC-BOM-MARINE-LINE': 'SFX Mumbai',
    'PB-UC-BOM-POWAI': 'SFX Mumbai',
    'PB-UC-BOM-MALAD-WEST': 'SFX Mumbai',
    'PB-UC-BOM-MALAD-EAST': 'SFX Mumbai',
    'PB-UC-HYD-MANIKONDA': 'SFX Hyderabad',
    # The "SFX MFCs" bucket, added to the sheet 2026-09-22 and live here from 2026-09-23. Held
    # out for a day in between: the Uniware login behind the UNIWARE_USERNAME secret had no
    # LOOKUP_INVENTORY grant on these six and answered HTTP 403, which is fatal by design and
    # took the whole snapshot down (run #178). Note for anyone adding a facility here: a code
    # showing as ENABLED in Uniware's facility listing does NOT mean the CI account can read its
    # inventory -- those are separate permissions, and only inventorySnapshot/get proves the
    # second one.
    'PB-UC-SFX-CHENNAI': 'SFX MFCs',
    'PB-UC-SFX-JAIPUR': 'SFX MFCs',
    'PB-UC-SFX-BHOPAL': 'SFX MFCs',
    'PB-UC-SFX-AHM': 'SFX MFCs',
    'PB-UC-SFX-LUCKNOW': 'SFX MFCs',
    'PB-UC-SFX-ZIRAKPUR': 'SFX MFCs',
}

# Every facility the Uniware snapshot has to cover, warehouses first.
ALL_UNIWARE_FACILITIES = (
    [code for codes in WAREHOUSE_FACILITY_CODES.values() for code in codes]
    + list(DARK_STORE_FACILITIES)
)

# Same alias map as parse_sop_master.js / parse_channel_dispatch_plan.js / parse_po_fulfillment.js.
SKU_ALIAS_MAP = {
    'M0': 'M0', 'NATIVE M0': 'M0',
    'M1': 'M1-2nd Gen', 'M1-2ND GEN': 'M1-2nd Gen', 'M1 AS': 'M1-2nd Gen', 'M1AS': 'M1-2nd Gen',
    'M1-AS': 'M1-2nd Gen', 'NATIVE M1 AS': 'M1-2nd Gen', 'M1 2ND GEN': 'M1-2nd Gen',
    'M1 PRO': 'M1 Pro', 'NATIVE M1 PRO': 'M1 Pro',
    'M2 PRO': 'M2 Pro', 'NATIVE M2 PRO': 'M2 Pro',
    'M3': 'M3', 'M3 PRO': 'M3 Pro', 'NATIVE M3': 'M3', 'NATIVE M3 PRO': 'M3 Pro',
}

MONTH_ABBR_TO_NUM = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def normalize_sku(raw):
    if raw is None:
        return None
    return SKU_ALIAS_MAP.get(str(raw).strip().upper())


def to_num(v):
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return 0.0


def normalize_date(v, default_year=None):
    """Parses a date cell into 'YYYY-MM-DD'. The Day wise trackr tab mixes AT LEAST two formats in
    the same column -- 'Jun 1 2026' (year included) and '1-Sep' (no year, seen further down the same
    sheet) -- so this tries several patterns rather than assuming one. For the year-less 'D-MMM' /
    'DD-MMM' form, `default_year` (the sync's own run year) is used -- correct for this sync's
    purpose (the sheet only ever carries the current +/- a few months of data), but would need
    revisiting if this tab ever wraps a calendar year boundary while this form is in use.
    Returns None (never a guess) if nothing matches -- callers must skip the row, not fabricate a
    date."""
    if v is None or v == "":
        return None
    s = str(v).strip()

    m = re.match(r'^([A-Za-z]{3})[a-z]*\s+(\d{1,2}),?\s+(\d{4})$', s)  # "Jun 1 2026" / "Jun 1, 2026"
    if m:
        mon = MONTH_ABBR_TO_NUM.get(m.group(1).lower())
        if mon:
            return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"

    m = re.match(r'^(\d{1,2})-([A-Za-z]{3})[a-z]*$', s)  # "1-Sep"
    if m and default_year:
        mon = MONTH_ABBR_TO_NUM.get(m.group(2).lower())
        if mon:
            return f"{default_year}-{mon:02d}-{int(m.group(1)):02d}"

    m = re.match(r'^(\d{2})/(\d{2})/(\d{2,4})', s)  # "DD/MM/YY" or "DD/MM/YYYY"
    if m:
        yy = m.group(3)
        yyyy = yy if len(yy) == 4 else "20" + yy
        return f"{yyyy}-{m.group(2)}-{m.group(1)}"

    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)  # already ISO
    if m:
        return m.group(0)

    return None


def get_access_token():
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        sys.exit("Missing GOOGLE_SERVICE_ACCOUNT_JSON environment variable.")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(sa_json)
        sa_path = f.name
    creds = service_account.Credentials.from_service_account_file(
        sa_path, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def get_values(token, spreadsheet_id, a1_range):
    """Sheets API values.get, default FORMATTED_VALUE rendering -- deliberately NOT
    UNFORMATTED_VALUE, so date-looking cells come back as display strings rather than
    ambiguous numeric serials (sidesteps the documented day<=12 dd/mm-vs-mm/dd misparse bug
    entirely -- there's no raw serial to misinterpret in the first place). Numbers with
    thousands separators (e.g. "16,632") also come back this way -- to_num() strips commas."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{a1_range}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("values", [])


def pad_row(row, length):
    """Sheets API omits trailing empty cells per row -- pad so column-index access never IndexErrors."""
    return row + [None] * (length - len(row))


def parse_daily_trackr_tab(rows, date_col, day_col_start):
    """Generic reader for the 4 single-channel daily tabs (UC sales trackr / Az / FK / MT - Daily
    trackr) -- port of parse_channel_dispatch_plan.js's parseDailyTrackrTab(). A row's date cell must
    parse (via normalize_date, handling both 'Jun 1 2026' and 'D-MMM' forms seen across these tabs);
    a date row present but every SKU cell blank is treated as no data for that date. Shared by
    sync_sop_dispatch_plan.py (Expected Sale + Planned Inward blocks) and sync_sop_sales.py (Expected
    Sale block, for the Sales: Plan vs Actual projection)."""
    import datetime
    current_year = datetime.date.today().year
    series = {}
    min_date, max_date = None, None
    for i in range(2, len(rows)):
        row = rows[i]
        date_cell = row[date_col] if date_col < len(row) else None
        ymd = normalize_date(date_cell, default_year=current_year)
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


DOI_DISPLAY_CAP_DAYS = 60


def add_days_ymd(ymd, days):
    import datetime
    return (datetime.date.fromisoformat(ymd) + datetime.timedelta(days=days)).isoformat()


def compute_forward_doi_from_series(series, max_known_ymd, start_ymd, sku, quantity, rate_multiplier=1.0,
                                     cap_days=DOI_DISPLAY_CAP_DAYS, cutoff_flag=None):
    """Port of computeForwardDOIFromSeries(): forward walk consuming daily rate until exhausted.
    Returns a float day count if exhausted within cap_days, or the literal string f">{cap_days}"
    otherwise -- whether that's because the walk ran past the series' own known forecast window, or
    the quantity is just genuinely large. Per Anish: hitting that ceiling only ever means "this SKU
    is so overstocked the forecast doesn't even reach far enough to exhaust it" -- one plain ">60"
    outcome reads better on a leadership-facing dashboard than distinguishing "insufficient data"
    from "400+" (the two outcomes this collapsed, before 2026-09-16).
    Shared by sync_sop_dispatch_plan.py (Target Closing / Required Dispatch) and
    sync_sop_inventory.py (sop_channel_drr_doi's DOI column).

    cutoff_flag (2026-09-23, ported from the /channel-dispatch-plan skill's 2026-09-22 change): when
    set, it's returned instead of f">{cap_days}" if the walk runs off the end of the known forecast
    before cap_days are confirmed -- fewer than cap_days of real demand were seen, so ">60" would
    claim more than the data proves. Only the dispatch plan passes it; the DRR/DOI heatmap keeps the
    single ">60" convention."""
    if not (quantity > 0):
        return 0.0
    remaining, ymd = quantity, start_ymd
    for days in range(1, cap_days + 1):
        ymd = add_days_ymd(ymd, 1)
        if not max_known_ymd or ymd > max_known_ymd:
            return cutoff_flag or f">{cap_days}"
        daily_rate = series.get(ymd, {}).get(sku, 0.0) * rate_multiplier
        if daily_rate <= 0:
            continue
        if remaining <= daily_rate:
            return (days - 1) + remaining / daily_rate
        remaining -= daily_rate
    return f">{cap_days}"


def parse_uc_sales_trackr_facility_block(rows, title_col):
    """Reads one per-facility 'Actual Sales' block from the 'UC sales trackr' tab. title_col is
    BOTH the block's title cell (row 0, e.g. 'PB-UC-BLR') AND its first SKU data column (row 1 has
    SKU headers M0/M1/M1 Pro/M2 Pro/M3/M3 Pro at title_col..title_col+5, Total at title_col+6) --
    a different block shape from parse_daily_trackr_tab's Expected Sale blocks, which have no title
    row occupying a data column. Verified live 2026-09-15: PB-UC-BLR@17, PB-UC-HYD@25,
    PB-UC-GGN@33, PB-UC-BOMBAY@41, PB-UC-KOL@49 (8-column stride, one blank separator column
    between blocks); the same stride continues rightward into the 21 individual dark-store blocks
    (discovered by find_trackr_title_cols below). Date column is always 0 (col A),
    same convention as every other daily tab."""
    import datetime
    current_year = datetime.date.today().year
    labels = ["M0", "M1", "M1 Pro", "M2 Pro", "M3", "M3 Pro"]
    series = {}
    min_date, max_date = None, None
    for i in range(2, len(rows)):
        row = rows[i]
        ymd = normalize_date(row[0] if row else None, default_year=current_year)
        if not ymd:
            continue
        raw = [row[c] if c < len(row) else None for c in range(title_col, title_col + 6)]
        if not any(v not in (None, "") for v in raw):
            continue
        by_sku = {normalize_sku(label): to_num(raw[j]) for j, label in enumerate(labels)}
        series[ymd] = by_sku
        if min_date is None or ymd < min_date:
            min_date = ymd
        if max_date is None or ymd > max_date:
            max_date = ymd
    return {"series": series, "min_date": min_date, "max_date": max_date}


def find_trackr_title_cols(rows, codes):
    """Maps each facility code to its "UC sales trackr" block title column, read off the tab's own
    header row rather than a hardcoded table.

    The blocks sit on an 8-column stride (7 data columns + 1 blank separator) and a block's title
    cell IS its first SKU column -- see parse_uc_sales_trackr_facility_block above. Those offsets
    used to be hardcoded per facility with nothing ever checking them against the title cell, so
    removing one block (a dark store closing) shifted every block to its right by 8 and each of
    those stores would silently have been read off its neighbour's numbers. Discovering them here
    keeps the sheet the single source of truth for its own layout.

    A code with no block is left out rather than defaulted: some tracked stores genuinely have none
    (e.g. PB-UC-BLR-CHAMRAJPET), and a newly opened store won't until someone adds it.
    Both cases mean on-hand but no DRR/DOI, which is the intended behaviour, not an error."""
    header = rows[0] if rows else []
    by_code = {}
    for col, cell in enumerate(header):
        code = str(cell or "").strip().upper()
        if code and code not in by_code:  # first match wins, as elsewhere in these parsers
            by_code[code] = col

    found, missing = {}, []
    for code in codes:
        col = by_code.get(str(code).strip().upper())
        if col is None:
            missing.append(code)
        else:
            found[code] = col
    if missing:
        print(f"NOTE: no 'UC sales trackr' block for {', '.join(missing)} -- on-hand only, "
              f"no DRR/DOI.", file=sys.stderr)
    return found


def fetch_uniware_on_hand(supabase_url, key):
    """Reads the live Uniware snapshot (sop_uniware_inventory, written by sync_uniware_inventory.py)
    and returns {facility_code: {sku: on_hand}}.

    Every on-hand figure in the S&OP section goes through this one read, so the Inventory Overview,
    the UC App + PLS tables, S&OP Planning and PO Fulfillment can't drift apart the way they did
    when each parsed the "Current Inventory" sheet with its own slightly different rules."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(f"{supabase_url}/rest/v1/sop_uniware_inventory",
                      headers=headers, params={"select": "facility,sku,on_hand"},
                      timeout=REQUEST_TIMEOUT)
    if not r.ok:
        sys.exit(f"Fetching sop_uniware_inventory failed ({r.status_code}): {r.text[:500]}")
    rows = r.json()
    if not rows:
        sys.exit("sop_uniware_inventory is empty -- run sync_uniware_inventory.py first. Refusing to "
                 "publish zeroed on-hand across the whole section.")
    by_facility = {}
    for row in rows:
        by_facility.setdefault(row["facility"], {})[row["sku"]] = to_num(row["on_hand"])
    return by_facility


def uniware_qty(by_facility, facility, sku):
    """One facility x SKU figure out of the snapshot. A facility missing entirely is a genuine
    problem rather than a zero -- sync_uniware_inventory.py aborts rather than publishing a partial
    snapshot, so this only fires if a facility is added to the mappings without re-running it."""
    if facility not in by_facility:
        print(f"WARNING: {facility} missing from the Uniware snapshot -- counting it as 0.",
              file=sys.stderr)
        return 0.0
    return by_facility[facility].get(sku, 0.0)


def uniware_warehouse_on_hand(by_facility):
    """{city: {sku: on_hand}} for the 5 mother warehouses -- Gurgaon and Kolkata each sum two
    real Uniware facilities."""
    return {
        city: {sku: sum(uniware_qty(by_facility, f, sku) for f in codes) for sku in SKUS}
        for city, codes in WAREHOUSE_FACILITY_CODES.items()
    }


def supabase_config():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY environment variables.")
    return url.rstrip("/"), key


def upsert(supabase_url, key, table, rows, on_conflict, ignore_duplicates=False):
    """Upsert via PostgREST. ignore_duplicates=True (Prefer: resolution=ignore-duplicates) is for
    write-once tables like production_plan_snapshots, where an existing row must never be
    overwritten; the default (merge-duplicates) is a normal upsert."""
    if not rows:
        sys.exit(f"Parsed zero rows for {table} -- aborting without touching it (a transient "
                  f"fetch/parse failure looks the same as an empty sheet; refusing to wipe "
                  f"existing data on that ambiguity).")
    resolution = "ignore-duplicates" if ignore_duplicates else "merge-duplicates"
    headers = {
        "apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "Prefer": f"resolution={resolution},return=minimal",
    }
    r = requests.post(
        f"{supabase_url}/rest/v1/{table}",
        headers=headers, params={"on_conflict": on_conflict}, json=rows, timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Upsert into {table} failed ({r.status_code}): {r.text[:500]}")


def replace_by_filter(supabase_url, key, table, rows, delete_params, allow_empty=False):
    """Wholesale replace scoped to a filter (e.g. {"run_date": "eq.2026-09-12"}) -- delete matching
    rows, then bulk insert. For tables where row COUNT varies run to run (so upsert's stable-key
    assumption doesn't hold), unlike the small/fixed-shape tables upsert() targets.

    allow_empty=True skips the zero-rows guard -- for tables where an empty result is a legitimate
    state (e.g. sop_po_action_items with no RESCHEDULE/PARTIAL this run is good news, not a fetch
    failure), the delete still runs (clearing out a stale prior run's rows) but no insert follows."""
    if not rows and not allow_empty:
        sys.exit(f"Parsed zero rows for {table} -- aborting without touching it (a transient "
                  f"fetch/parse failure looks the same as an empty sheet; refusing to wipe "
                  f"existing data on that ambiguity).")
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r = requests.delete(f"{supabase_url}/rest/v1/{table}", headers=headers, params=delete_params,
                         timeout=REQUEST_TIMEOUT)
    if not r.ok:
        sys.exit(f"Clearing {table} failed ({r.status_code}): {r.text[:500]}")
    if not rows:
        return
    r = requests.post(f"{supabase_url}/rest/v1/{table}", headers={**headers, "Prefer": "return=minimal"},
                       json=rows, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        sys.exit(f"Insert into {table} failed ({r.status_code}): {r.text[:500]}")
