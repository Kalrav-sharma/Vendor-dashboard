#!/usr/bin/env python3
"""
S&OP: Inventory Overview tab.

Pulls WH-Channel-SKU's "Current Inventory" tab (channel x SKU matrix, plus
the "UC App - RO" block's per-city on-hand rows) and Copy Daily Input
Anish's "Dispatch Planning" tab (the "Intransit Inventory" block's
per-warehouse in-transit rows) via a Google service account (Sheets API),
and upserts the result into Supabase's sop_inventory_channel /
sop_inventory_uc_warehouse tables. Powers the portal's S&OP > Inventory
Overview page.

This is a direct port of parseChannelInventoryOverview() and
parseDispatchPlanningBlock() in
~/.claude/scripts/parse_sop_master.js -- same header-label-driven column
detection (never hardcoded column letters), same "Grand Total" sentinel
stop, same first-match-wins SKU-column guard (a real, already-fixed bug in
an earlier hardcoded version silently let a lookalike second table's
ambiguous "M1AS" column overwrite the correct one -- see that guard below).

Runs on a schedule (every 30 min, business hours IST) via the "Sync S&OP
inventory" GitHub Actions workflow; also has workflow_dispatch for a manual
run.

Credentials, as GitHub Actions repo secrets, never committed (all three
already provisioned for sync_mm_rate_card.py -- no new secrets needed):
  GOOGLE_SERVICE_ACCOUNT_JSON  -- already shared as Editor on both sheets
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""
import os
import sys
import tempfile

import google.auth.transport.requests
import requests
from google.oauth2 import service_account

WH_CHANNEL_SKU_ID = "17VKM18L9vlo72LtcKvTOmCbcWqJaRc3sSJIwhthbW8U"
COPY_DAILY_INPUT_ANISH_ID = "1e04j1Z77tp1dp-b45WSBE_fa-d_y38WlbHQPyeVOVzU"
REQUEST_TIMEOUT = 30

SKUS = ['M0', 'M1-2nd Gen', 'M1 Pro', 'M2 Pro', 'M3', 'M3 Pro']

# Same alias map as parse_sop_master.js / parse_channel_dispatch_plan.js / parse_po_fulfillment.js.
SKU_ALIAS_MAP = {
    'M0': 'M0', 'NATIVE M0': 'M0',
    'M1': 'M1-2nd Gen', 'M1-2ND GEN': 'M1-2nd Gen', 'M1 AS': 'M1-2nd Gen', 'M1AS': 'M1-2nd Gen',
    'M1-AS': 'M1-2nd Gen', 'NATIVE M1 AS': 'M1-2nd Gen', 'M1 2ND GEN': 'M1-2nd Gen',
    'M1 PRO': 'M1 Pro', 'NATIVE M1 PRO': 'M1 Pro',
    'M2 PRO': 'M2 Pro', 'NATIVE M2 PRO': 'M2 Pro',
    'M3': 'M3', 'M3 PRO': 'M3 Pro', 'NATIVE M3': 'M3', 'NATIVE M3 PRO': 'M3 Pro',
}

# WH-Channel-SKU "Current Inventory" tab channel-block labels -> display channel name. "DTDC Gurgaon"
# is the sheet's real label (it also holds Delhi-coded PB-UC-DEL-* facilities) -- not renamed to
# "DTDC Delhi", matching the existing /sop-master convention.
INVENTORY_CHANNEL_MAP = {
    'UC APP - RO': 'UC App+PLS',
    'AMAZON': 'Amazon',
    'FLIPKART': 'Flipkart',
    'DTDC BANGALORE': 'DTDC Bangalore',
    'DTDC GURGAON': 'DTDC Gurgaon',
    'DTDC KOLKATA': 'DTDC Kolkata',
    'SFX MUMBAI': 'SFX Mumbai',
    'SFX HYDERABAD': 'SFX Hyderabad',
    'CROMA': 'Croma',
    'VIJAY SALES': 'Vijay Sales',
}
INV_CHANNELS = list(INVENTORY_CHANNEL_MAP.values())
WAREHOUSES = ['Bangalore', 'Gurgaon', 'Hyderabad', 'Mumbai', 'Kolkata']

# Facility code -> warehouse city, reused as-is from the JS scripts.
WH_CODE_MAP = {
    'PB-UC-BLR': 'Bangalore',
    'PB-UC-BOMBAY': 'Mumbai', 'PB-UC-MUM': 'Mumbai', 'PB-UC-BOM': 'Mumbai',
    'PB-UC-GGN': 'Gurgaon', 'PB-UC-GGN-PATAUDI': 'Gurgaon', 'PB-UC-GGN_PATAUDI': 'Gurgaon',
    'PB-UC-HYD': 'Hyderabad',
    'PB-UC-KOL': 'Kolkata', 'PB-UC-KOL-PANCHLA': 'Kolkata', 'PB-UC-KOL-PANCHALA': 'Kolkata',
}


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
    entirely, since there's no raw serial to misinterpret in the first place)."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{a1_range}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("values", [])


def pad_row(row, length):
    """Sheets API omits trailing empty cells per row -- pad so column-index access never IndexErrors."""
    return row + [None] * (length - len(row))


def parse_channel_inventory_overview(rows):
    """Port of parse_sop_master.js's parseChannelInventoryOverview(). Returns
    (channel_rows, uc_city_rows) where channel_rows is [{channel, sku, qty}, ...] across all 10
    buckets, and uc_city_rows is [{warehouse, sku, on_hand}, ...] for the UC App+PLS channel's 5
    city rows specifically (the "UC App - RO" block), unsummed."""
    channel_totals = {ch: {s: 0.0 for s in SKUS} for ch in INV_CHANNELS}
    uc_city_on_hand = {wh: {s: 0.0 for s in SKUS} for wh in WAREHOUSES}

    header_row_idx = -1
    sku_col_map = {}
    for i in range(min(10, len(rows))):
        row = pad_row(rows[i], 2)
        if str(row[0] or "").strip().lower() == "channel" and str(row[1] or "").strip().lower() == "city":
            header_row_idx = i
            for c in range(2, len(rows[i])):
                sku = normalize_sku(rows[i][c])
                # first match wins -- never let a later duplicate-looking header overwrite an
                # already-mapped column (the historical "M1AS" ambiguous-column bug this guards
                # against was exactly this kind of overwrite, in a sibling block further right)
                if sku and sku not in sku_col_map:
                    sku_col_map[sku] = c
            break
    if header_row_idx == -1:
        print("WARNING: could not find 'Channel'/'City' header row in Current Inventory tab", file=sys.stderr)
        return [], []

    current_channel = ""
    for i in range(header_row_idx + 1, len(rows)):
        row = pad_row(rows[i], 2)
        chan_val = str(row[0] or "").strip()
        city_val = str(row[1] or "").strip()
        # "In transit Inventory" section starts right after the last channel block (Vijay Sales),
        # no channel label of its own, City column literally reads this -- hard stop, everything
        # after is a different table (facility rows, not city rows).
        if city_val.lower() == "in transit inventory":
            break
        if chan_val:
            current_channel = chan_val
        if not city_val or city_val.lower() in ("total", "grand total"):
            continue
        bucket = INVENTORY_CHANNEL_MAP.get(current_channel.upper())
        if not bucket:
            continue
        for sku in SKUS:
            col = sku_col_map.get(sku)
            if col is None or col >= len(row):
                continue
            qty = to_num(row[col])
            channel_totals[bucket][sku] += qty
            if bucket == "UC App+PLS" and city_val in uc_city_on_hand:
                uc_city_on_hand[city_val][sku] += qty

    channel_rows = [{"channel": ch, "sku": s, "qty": channel_totals[ch][s]}
                     for ch in INV_CHANNELS for s in SKUS]
    uc_rows = [{"warehouse": wh, "sku": s, "on_hand": uc_city_on_hand[wh][s]}
               for wh in WAREHOUSES for s in SKUS]
    return channel_rows, uc_rows


def parse_uc_warehouse_in_transit(rows):
    """Port of parse_sop_master.js's parseDispatchPlanningBlock(wbPO, 'Intransit Inventory').
    Returns {warehouse: {sku: qty}}. Stops hard at the literal "Grand Total" sentinel row -- rows
    below it are an unrelated Native Lock stock dump reusing the same PB-UC-* facility-code
    prefixes (including DTDC dark-store variants), and would otherwise silently contaminate these
    totals."""
    result = {wh: {s: 0.0 for s in SKUS} for wh in WAREHOUSES}
    block_title = "intransit inventory"

    title_row, title_col = -1, -1
    for i in range(min(5, len(rows))):
        for c, v in enumerate(rows[i]):
            if str(v or "").strip().lower() == block_title:
                title_row, title_col = i, c
                break
        if title_row != -1:
            break
    if title_row == -1:
        print('WARNING: could not find "Intransit Inventory" block in Dispatch Planning tab', file=sys.stderr)
        return result

    header_row = rows[title_row + 1] if title_row + 1 < len(rows) else []
    facility_col = -1
    sku_col_map = {}
    for c in range(title_col, len(header_row)):
        s = str(header_row[c] or "").strip()
        sl = s.lower()
        if facility_col == -1 and sl in ("facility", "to"):
            facility_col = c
            continue
        if sl in ("m1as", "m1 as"):
            if "M1-2nd Gen" not in sku_col_map:
                sku_col_map["M1-2nd Gen"] = c
            continue
        sku = normalize_sku(s)
        if sku and sku not in sku_col_map:
            sku_col_map[sku] = c
    if facility_col == -1:
        print('WARNING: could not find facility column for "Intransit Inventory" block', file=sys.stderr)
        return result

    for i in range(title_row + 2, len(rows)):
        row = rows[i]
        cell = row[facility_col] if facility_col < len(row) else None
        fac_val = str(cell or "").strip()
        if not fac_val:
            continue
        if fac_val.lower() == "grand total":
            break  # hard stop -- see function docstring
        city = WH_CODE_MAP.get(fac_val.upper())
        if not city:
            continue
        for sku in SKUS:
            col = sku_col_map.get(sku)
            if col is not None and col < len(row):
                result[city][sku] += to_num(row[col])
    return result


def supabase_config():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY environment variables.")
    return url.rstrip("/"), key


def upsert(supabase_url, key, table, rows, on_conflict):
    if not rows:
        sys.exit(f"Parsed zero rows for {table} -- aborting without touching it (a transient "
                  f"fetch/parse failure looks the same as an empty sheet; refusing to wipe "
                  f"existing data on that ambiguity).")
    headers = {
        "apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    r = requests.post(
        f"{supabase_url}/rest/v1/{table}",
        headers=headers, params={"on_conflict": on_conflict}, json=rows, timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Upsert into {table} failed ({r.status_code}): {r.text[:500]}")


def main():
    supabase_url, supabase_key = supabase_config()
    token = get_access_token()

    inv_rows = get_values(token, WH_CHANNEL_SKU_ID, "'Current Inventory'")
    dispatch_rows = get_values(token, COPY_DAILY_INPUT_ANISH_ID, "'Dispatch Planning'")

    channel_rows, uc_on_hand_rows = parse_channel_inventory_overview(inv_rows)
    uc_in_transit = parse_uc_warehouse_in_transit(dispatch_rows)

    on_hand_by_key = {(r["warehouse"], r["sku"]): r["on_hand"] for r in uc_on_hand_rows}
    uc_warehouse_rows = [
        {"warehouse": wh, "sku": s,
         "on_hand": on_hand_by_key.get((wh, s), 0.0),
         "in_transit": uc_in_transit[wh][s]}
        for wh in WAREHOUSES for s in SKUS
    ]

    upsert(supabase_url, supabase_key, "sop_inventory_channel", channel_rows, "channel,sku")
    upsert(supabase_url, supabase_key, "sop_inventory_uc_warehouse", uc_warehouse_rows, "warehouse,sku")

    total_channel_qty = sum(r["qty"] for r in channel_rows)
    total_uc_combined = sum(r["on_hand"] + r["in_transit"] for r in uc_warehouse_rows)
    print(f"Synced {len(channel_rows)} channel-inventory row(s) (total qty {total_channel_qty:.0f}) "
          f"and {len(uc_warehouse_rows)} UC-warehouse row(s) (total on-hand+in-transit {total_uc_combined:.0f}).")


if __name__ == "__main__":
    main()
