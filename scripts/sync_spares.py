#!/usr/bin/env python3
"""
Spares section feed: the "Spare automations" sheet + a live Uniware good/bad snapshot.

    "SKU list and uni data" tab  -> spares_sku_master   (category, vendor, DRR, in transit,
                                                         delivery date, next dispatch -- per WH)
    Uniware inventorySnapshot    -> spares_wh_inventory (good + bad, every SKU, 7 facilities)

DOI and "Required qty basis 60 DOI" are deliberately NOT taken from the sheet: its PB_GGN /
PB_KOL columns ignore Pataudi and Panchla stock, and the portal clubs them (GGN = GGN + Pataudi,
KOL = KOL + Panchla). The frontend recomputes both from this table's stock and the sheet's DRR.

Sheet columns are located from the tab's own two header rows (block title on row 2, warehouse
code on row 3), never hardcoded -- a column inserted in the sheet must not silently shift one
block's numbers onto another's.

"Every SKU at the warehouse": inventorySnapshot/get needs either an explicit SKU list or
updatedSinceInMinutes. We do both and merge -- the explicit list (sheet SKUs + the Jarvis spares
list in "DRR Raw" + our machine SKUs) guarantees every SKU the business tracks, and the
updatedSinceInMinutes call picks up anything else lying in the warehouse. If Uniware rejects the
latter, the run carries on with the explicit list and says so in the log.

Flags:
  --dry-run        read everything, print a summary, write nothing
  --skip-uniware   sheet only (local testing without Uniware credentials)

Env: GOOGLE_SERVICE_ACCOUNT_JSON, UNIWARE_USERNAME, UNIWARE_PASSWORD, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY.
"""
import sys
import time

import requests

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import (  # noqa: E402
    REQUEST_TIMEOUT, UNIWARE_SKU_MAP, WAREHOUSE_FACILITY_CODES, get_access_token,
    pad_row, replace_by_filter, supabase_config, to_num,
)
from sync_uniware_inventory import (  # noqa: E402
    EMPTY_FACILITY_ERROR_CODE, FACILITY_RETRY_ATTEMPTS, FACILITY_RETRY_BACKOFF_SECONDS,
    INVENTORY_PATH, UNIWARE_BASE_URL, get_uniware_token, uniware_headers,
)

SPARES_SHEET_ID = "1Eb8fqROZLM2sw2_6GbWo75AqtzPK5dvTVc1vwV8FOz0"
SKU_TAB = "SKU list and uni data"
DRR_RAW_TAB = "DRR Raw"

WH_KEYS = {"PB_GGN": "ggn", "PB_BLR": "blr", "PB_BOM": "bom", "PB_KOL": "kol", "PB_HYD": "hyd"}
FACILITIES = [code for codes in WAREHOUSE_FACILITY_CODES.values() for code in codes]

SKU_CHUNK = 100                      # keep request bodies small; Uniware's cap is undocumented
UPDATED_SINCE_MINUTES = 60 * 24 * 365 * 5

# Codes Uniware rejected at one facility are unknown to the tenant, so skip them at the rest.
UNKNOWN_CODES = set()


SHEET_ATTEMPTS = 5
SHEET_TIMEOUT = 120


def get_values(token, spreadsheet_id, a1_range):
    """sop_common.get_values with patience: this workbook is formula-heavy and the Sheets API
    answers it with 503 / slow reads at times (seen 2026-09-28). Same FORMATTED_VALUE rendering."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{a1_range}"
    last = None
    for attempt in range(1, SHEET_ATTEMPTS + 1):
        try:
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=SHEET_TIMEOUT)
            if resp.status_code < 500 and resp.status_code != 429:
                resp.raise_for_status()
                return resp.json().get("values", [])
            last = f"HTTP {resp.status_code}"
        except requests.exceptions.RequestException as e:
            if isinstance(e, requests.exceptions.HTTPError):
                raise
            last = type(e).__name__
        print(f"WARN: Sheets read {a1_range} attempt {attempt}/{SHEET_ATTEMPTS}: {last}", file=sys.stderr)
        if attempt < SHEET_ATTEMPTS:
            time.sleep(15 * attempt)
    sys.exit(f"Sheets read {a1_range} failed after {SHEET_ATTEMPTS} attempts ({last}).")


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def locate_columns(title_row, code_row):
    """Maps the tab's header rows to column indexes. Returns {field: index}."""
    title_row = [(c or "").strip().lower() for c in title_row]
    code_row = [(c or "").strip() for c in code_row]
    width = max(len(title_row), len(code_row))
    title_row = pad_row(title_row, width)
    code_row = pad_row(code_row, width)

    titles = [(i, t) for i, t in enumerate(title_row) if t]

    def block(title):
        for n, (i, t) in enumerate(titles):
            if t == title:
                end = titles[n + 1][0] if n + 1 < len(titles) else width
                return i, end
        sys.exit(f"Header block '{title}' not found in row 2 of '{SKU_TAB}' -- has the sheet layout changed?")

    cols = {}
    for field, wanted in (("sku", "SKU"), ("category", "Category"), ("vendor", "Vendor")):
        try:
            cols[field] = code_row.index(wanted)
        except ValueError:
            sys.exit(f"Column '{wanted}' not found in row 3 of '{SKU_TAB}'.")

    for title, prefix in (("drr", "drr"), ("in transit", "in_transit"), ("delivery date", "delivery")):
        start, end = block(title)
        for i in range(start, end):
            if code_row[i] in WH_KEYS:
                cols[f"{prefix}_{WH_KEYS[code_row[i]]}"] = i
        missing = [k for k in WH_KEYS.values() if f"{prefix}_{k}" not in cols]
        if missing:
            sys.exit(f"Block '{title}' is missing warehouse column(s) {missing}.")
        if title == "drr":
            for i in range(start, end):
                if code_row[i].lower() == "total drr":
                    cols["total_drr"] = i
    cols["next_dispatch_date"] = block("next dispatch")[0]
    cols["next_dispatch_qty"] = block("dispatch qty")[0]
    return cols


def read_sheet(token):
    header = get_values(token, SPARES_SHEET_ID, f"'{SKU_TAB}'!A2:CZ3")
    if len(header) < 2:
        sys.exit(f"'{SKU_TAB}' header rows 2-3 came back empty.")
    cols = locate_columns(header[0], header[1])
    print("Columns: " + ", ".join(f"{k}={col_letter(v)}" for k, v in sorted(cols.items(), key=lambda kv: kv[1])))

    width = max(cols.values()) + 1
    # Only as wide as we need -- this tab is formula-heavy and wide reads are what time out.
    rows = get_values(token, SPARES_SHEET_ID, f"'{SKU_TAB}'!A4:{col_letter(width - 1)}1000")
    out, seen = [], set()
    for n, raw in enumerate(rows):
        r = pad_row(raw, width)
        sku = (r[cols["sku"]] or "").strip()
        if not sku or sku in seen:
            continue
        seen.add(sku)

        def txt(key):
            v = (r[cols[key]] or "").strip()
            return v or None

        rec = {
            "sku": sku,
            "category": txt("category"),
            "sheet_vendor": txt("vendor"),
            "total_drr": to_num(r[cols["total_drr"]]) if "total_drr" in cols else 0.0,
            "next_dispatch_date": txt("next_dispatch_date"),
            "next_dispatch_qty": to_num(r[cols["next_dispatch_qty"]]) if txt("next_dispatch_qty") else None,
            "sheet_order": n,
        }
        for k in WH_KEYS.values():
            rec[f"drr_{k}"] = to_num(r[cols[f"drr_{k}"]])
            rec[f"in_transit_{k}"] = to_num(r[cols[f"in_transit_{k}"]])
            rec[f"delivery_{k}"] = txt(f"delivery_{k}")
        if "total_drr" not in cols:
            rec["total_drr"] = sum(rec[f"drr_{k}"] for k in WH_KEYS.values())
        out.append(rec)
    return out


def read_drr_raw_skus(token):
    rows = get_values(token, SPARES_SHEET_ID, f"'{DRR_RAW_TAB}'!A1:I5000")
    if not rows:
        return set()
    hdr = [(c or "").strip().upper() for c in rows[0]]
    if "ITEM_TYPE_SKU" not in hdr:
        print(f"WARN: '{DRR_RAW_TAB}' has no ITEM_TYPE_SKU column; skipping it.", file=sys.stderr)
        return set()
    i = hdr.index("ITEM_TYPE_SKU")
    return {(pad_row(r, i + 1)[i] or "").strip() for r in rows[1:]} - {""}


def snapshot_call(token, facility, body):
    """One inventorySnapshot/get call -> {sku: (good, bad)}, or None when Uniware rejects the body."""
    last = None
    for attempt in range(1, FACILITY_RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(f"{UNIWARE_BASE_URL}{INVENTORY_PATH}",
                                 headers=uniware_headers(token, facility), json=body,
                                 timeout=REQUEST_TIMEOUT * 2)
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            payload = resp.json()
            if not payload.get("successful", False):
                codes = {e.get("code") for e in (payload.get("errors") or [])}
                if codes == {EMPTY_FACILITY_ERROR_CODE}:
                    return {}
                # Application-level rejection (e.g. an SKU code Uniware doesn't know). Not retried:
                # the caller decides whether to split the request or give up.
                print(f"NOTE: {facility}: rejected {list(body)[0]}: {str(payload.get('errors'))[:200]}",
                      file=sys.stderr)
                return None
            out = {}
            for snap in payload.get("inventorySnapshots") or []:
                sku = snap.get("itemTypeSKU")
                if not sku:
                    continue
                g, b = out.get(sku, (0.0, 0.0))
                out[sku] = (g + float(snap.get("inventory") or 0), b + float(snap.get("badInventory") or 0))
            return out
        except RuntimeError as e:
            last = e
        except requests.exceptions.RequestException as e:
            last = e
        print(f"WARN: {facility} attempt {attempt}/{FACILITY_RETRY_ATTEMPTS}: {last}", file=sys.stderr)
        if attempt < FACILITY_RETRY_ATTEMPTS:
            time.sleep(FACILITY_RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"{facility}: giving up after {FACILITY_RETRY_ATTEMPTS} attempts -- {last}")


def fetch_facility(token, facility, sku_universe):
    """Good/bad for every SKU at one facility: explicit list (chunked) merged with updatedSince."""
    merged = {}
    skus = sorted(set(sku_universe) - UNKNOWN_CODES)
    rejected = []
    for i in range(0, len(skus), SKU_CHUNK):
        chunk = skus[i:i + SKU_CHUNK]
        got = snapshot_call(token, facility, {"itemTypeSKUs": chunk})
        if got is None:
            # One unknown code can sink a whole chunk -- fall back to one call per SKU (~0.1s each).
            got = {}
            for sku in chunk:
                one = snapshot_call(token, facility, {"itemTypeSKUs": [sku]})
                if one is None:
                    rejected.append(sku)
                else:
                    got.update(one)
        merged.update(got)
    UNKNOWN_CODES.update(rejected)
    if skus and len(rejected) == len(skus):
        raise RuntimeError(f"{facility}: Uniware rejected every SKU in the explicit list.")
    if rejected:
        print(f"NOTE: {facility}: {len(rejected)} code(s) unknown to Uniware, skipped: {rejected[:15]}",
              file=sys.stderr)
    explicit_n = len(merged)

    extra = snapshot_call(token, facility, {"updatedSinceInMinutes": UPDATED_SINCE_MINUTES})
    added = 0
    if extra is None:
        print(f"NOTE: {facility}: Uniware rejected updatedSinceInMinutes -- explicit SKU list only.",
              file=sys.stderr)
    else:
        for sku, v in extra.items():
            if sku not in merged:
                merged[sku] = v
                added += 1
    print(f"  {facility}: {explicit_n} SKU(s) from explicit list, +{added} from updatedSince", flush=True)
    return merged


def main():
    dry_run = "--dry-run" in sys.argv
    skip_uniware = "--skip-uniware" in sys.argv

    gtoken = get_access_token()
    master = read_sheet(gtoken)
    cats = {}
    for m in master:
        cats[m["category"] or "(blank)"] = cats.get(m["category"] or "(blank)", 0) + 1
    print(f"Sheet: {len(master)} SKU(s); categories {cats}")

    inv_rows = []
    if not skip_uniware:
        universe = {m["sku"] for m in master} | read_drr_raw_skus(gtoken) | set(UNIWARE_SKU_MAP)
        print(f"Uniware: explicit SKU universe {len(universe)}")
        utoken = get_uniware_token()
        for facility in FACILITIES:
            # Not caught: a facility that can't be read must fail the run rather than publish a
            # snapshot silently missing a warehouse. The previous snapshot stays live meanwhile.
            by_sku = fetch_facility(utoken, facility, universe)
            inv_rows.extend({"facility": facility, "sku": sku, "good_qty": g, "bad_qty": b}
                            for sku, (g, b) in by_sku.items() if g or b)
        for f in FACILITIES:
            fr = [r for r in inv_rows if r["facility"] == f]
            print(f"  {f}: {len(fr)} stocked SKU(s), good {sum(r['good_qty'] for r in fr):.0f}, "
                  f"bad {sum(r['bad_qty'] for r in fr):.0f}")

    if dry_run:
        print("--dry-run: nothing written.")
        return

    url, key = supabase_config()
    replace_by_filter(url, key, "spares_sku_master", master, {"sku": "not.is.null"})
    if not skip_uniware:
        replace_by_filter(url, key, "spares_wh_inventory", inv_rows, {"facility": "not.is.null"})
    print(f"Synced {len(master)} master row(s)" + ("" if skip_uniware else f", {len(inv_rows)} inventory row(s)") + ".")


if __name__ == "__main__":
    main()
