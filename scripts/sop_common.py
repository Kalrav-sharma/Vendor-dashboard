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


def replace_by_filter(supabase_url, key, table, rows, delete_params):
    """Wholesale replace scoped to a filter (e.g. {"run_date": "eq.2026-09-12"}) -- delete matching
    rows, then bulk insert. For tables where row COUNT varies run to run (so upsert's stable-key
    assumption doesn't hold), unlike the small/fixed-shape tables upsert() targets."""
    if not rows:
        sys.exit(f"Parsed zero rows for {table} -- aborting without touching it (a transient "
                  f"fetch/parse failure looks the same as an empty sheet; refusing to wipe "
                  f"existing data on that ambiguity).")
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r = requests.delete(f"{supabase_url}/rest/v1/{table}", headers=headers, params=delete_params,
                         timeout=REQUEST_TIMEOUT)
    if not r.ok:
        sys.exit(f"Clearing {table} failed ({r.status_code}): {r.text[:500]}")
    r = requests.post(f"{supabase_url}/rest/v1/{table}", headers={**headers, "Prefer": "return=minimal"},
                       json=rows, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        sys.exit(f"Insert into {table} failed ({r.status_code}): {r.text[:500]}")
