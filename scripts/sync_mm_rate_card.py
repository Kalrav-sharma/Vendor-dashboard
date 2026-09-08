#!/usr/bin/env python3
"""
Pulls the "Native - Commercials" Google Sheet's "Mid mile commercials" tab
via a Google service account (Sheets API), computes the same "Effective
Rate Card" derivation the vendor-adherence-weekly-refresh skill's
sync_rate_card.py does (collapse Lets-Transport rate-variant columns to
one authoritative column, compute a case-insensitive Lane Key and the
cheapest quoted price per lane), and wholesale REPLACES Supabase's
mm_rate_card table with the result (delete-all then insert-fresh, not an
upsert -- see replace_rows() for why). Powers the portal's Rate Finder page.

Manual, on-demand only -- run via the "Sync Mid Mile rate card" GitHub
Actions workflow's "Run workflow" button, same convention the source
skill already uses for this exact rate card ("only resync when Kalrav
says the commercials changed" -- see that skill's SKILL.md Step 1b).
There is deliberately no schedule for this script.

Credentials, as GitHub Actions repo secrets, never committed:
  GOOGLE_SERVICE_ACCOUNT_JSON  -- the service account key file's raw JSON
                                  content (same service account already
                                  used for the Daily Input tracker pull --
                                  it must additionally be shared as Viewer
                                  on the "Native - Commercials" sheet)
  SUPABASE_URL                 -- already set for the Uniware sync
  SUPABASE_SERVICE_ROLE_KEY    -- already set for the Uniware sync
"""
import json
import os
import re
import sys
import tempfile

import google.auth.transport.requests
import requests
from google.oauth2 import service_account

SPREADSHEET_ID = "1Dnyhe8uckZX2V6HLrSHdd7Re3xmoo4eUGRBg04d2TVw"
SHEET_NAME = "Mid mile commercials"
REQUEST_TIMEOUT = 30

LT_VARIANTS = ["Lets-Transport Final", "Lets-Transport Latest Rates",
               "Lets-Transport New Rates", "Lets-Transport"]
LT_CANONICAL = "Lets-Transport"


def num(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def fetch_sheet_values():
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
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/'{SHEET_NAME}'"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {creds.token}"},
        params={"valueRenderOption": "UNFORMATTED_VALUE"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("values", [])


def read_lanes(values):
    if not values:
        sys.exit("Sheets API returned zero rows for 'Mid mile commercials' -- aborting.")
    header = [str(v).strip() if v is not None else "" for v in values[0]]
    vendor_cols = [(h, i) for i, h in enumerate(header) if i >= 3 and h]

    lanes = []
    for row in values[1:]:
        if len(row) < 3 or not row[0] or not row[1] or not row[2]:
            continue
        prices = {h: num(row[i]) if i < len(row) else None for h, i in vendor_cols}
        lanes.append((str(row[0]).strip(), str(row[1]).strip(), str(row[2]).strip(), prices))
    return [h for h, _ in vendor_cols], lanes


def effective_vendors(vendor_names):
    out = []
    for v in vendor_names:
        if v in LT_VARIANTS:
            if LT_CANONICAL not in out:
                out.append(LT_CANONICAL)
        else:
            out.append(v)
    return out


def effective_price(prices, vendor):
    if vendor != LT_CANONICAL:
        return prices.get(vendor)
    for variant in LT_VARIANTS:  # authoritative order, first hit wins
        if prices.get(variant) is not None:
            return prices[variant]
    return None


def lane_key(origin, destination, truck_size):
    # Case-insensitive matching -- the source sheet has inconsistent casing
    # (e.g. "AHMEDABAD" vs "Ahmedabad"), a documented past bug in the
    # sibling adherence skill (reference/METHODOLOGY.md). Collapse
    # whitespace too, for the same reason.
    parts = [re.sub(r"\s+", " ", p.strip()).upper() for p in (origin, destination, truck_size)]
    return "|".join(parts)


def build_rows(vendor_names, lanes):
    eff_vendors = effective_vendors(vendor_names)
    rows = []
    for origin, destination, truck_size, prices in lanes:
        eff_prices = {v: effective_price(prices, v) for v in eff_vendors}
        quoted = {v: p for v, p in eff_prices.items() if p is not None}
        cheapest_vendor, cheapest_price = None, None
        if quoted:
            cheapest_vendor = min(quoted, key=quoted.get)
            cheapest_price = quoted[cheapest_vendor]
        rows.append({
            "lane_key": lane_key(origin, destination, truck_size),
            "origin": origin, "destination": destination, "truck_size": truck_size,
            "vendor_rates": eff_prices,
            "cheapest_vendor": cheapest_vendor,
            "cheapest_price": cheapest_price,
        })
    return rows


def supabase_config():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY environment variables.")
    return url.rstrip("/"), key


def replace_rows(supabase_url, key, rows):
    """Wholesale replace -- deletes every existing mm_rate_card row, then
    inserts the fresh set. An upsert-only sync (the original approach here)
    never removes a row whose lane no longer exists in the current sheet --
    e.g. renaming a truck-size label ("10-FT" -> "10FT") leaves the OLD
    label's row behind forever, showing up as a phantom duplicate in Rate
    Finder's dropdowns. The rate card is meant to always mirror the sheet's
    CURRENT state exactly (same "one rate card, not one per week" doctrine
    the sibling vendor-adherence skill's sync_rate_card.py already documents
    for this same spreadsheet), so a full replace on every sync is correct,
    not just simpler.

    Refuses to run if `rows` came back empty -- almost certainly a transient
    Sheets API / parsing failure, and wiping a good table because of that
    would be worse than just leaving stale data one run longer."""
    if not rows:
        sys.exit("Parsed zero lanes from the sheet -- aborting without touching mm_rate_card "
                  "(a transient fetch/parse failure looks the same as an empty sheet; refusing "
                  "to wipe existing data on that ambiguity).")

    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    r = requests.delete(
        f"{supabase_url}/rest/v1/mm_rate_card",
        headers=headers,
        params={"lane_key": "not.is.null"},  # matches every row -- lane_key is the (non-null) primary key
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Clearing mm_rate_card failed ({r.status_code}): {r.text[:500]}")

    r = requests.post(
        f"{supabase_url}/rest/v1/mm_rate_card",
        headers={**headers, "Prefer": "return=minimal"},
        json=rows,
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        sys.exit(f"Insert into mm_rate_card failed ({r.status_code}): {r.text[:500]}")


def main():
    supabase_url, supabase_key = supabase_config()
    vendor_names, lanes = read_lanes(fetch_sheet_values())
    rows = build_rows(vendor_names, lanes)
    replace_rows(supabase_url, supabase_key, rows)
    uncovered = sum(1 for r in rows if r["cheapest_vendor"] is None)
    print(f"Synced {len(rows)} lane(s) into mm_rate_card ({uncovered} with no vendor quoted).")
    print(f"Vendor columns ({len(vendor_names)}): {', '.join(vendor_names)}")


if __name__ == "__main__":
    main()
