#!/usr/bin/env python3
"""
Populates po_invoice_uploads.payment_status / payment_date / payment_ref /
payment_synced_at from Finance's weekly "Payout File" Oracle Fusion AP
export (an .xlsx workbook, one row per invoice line, covering every UC
vendor -- not just this portal's) -- these columns already exist in
schema.sql (see the comment above them) but nothing has ever written to
them, because the originally-planned Jarvis sync for this was never
built. This script is the substitute: same target columns, same
write path (service_role, no INSERT/UPDATE policy for authenticated),
just a manual file drop instead of a live API pull.

RUN MANUALLY, LOCALLY -- never in GitHub Actions. The payout file lives
in Finance's own OneDrive folder on a laptop, never in this repo (and
should not be committed -- it contains bank account numbers and IFSC
codes for every UC vendor company-wide, not just this portal's two).
Re-run it whenever a new weekly Payout File lands; each file is a full
cumulative AP ledger (rows going back over a year), so the latest file
alone is always the authoritative current snapshot -- there's no need to
feed it older files too.

Matching: a po_invoice_uploads row only has a payment status to report if
it has been through the AI invoice-match check (check-invoice-match Edge
Function) and got an invoice number out of the PDF (match_details.
extracted.invoice_number) -- that's matched, on (vendor_code, invoice
number), against the payout file's "Invoice Num" column. Vendor identity
comes from comparing the payout file's "Vendor Name" against this
portal's profiles.vendor_name (role='vendor'), after stripping the legal-
entity suffix ("Pvt Ltd" vs "Private Limited" etc. -- Finance's Oracle
export and our profiles table don't always spell the same vendor's name
the same way).

There is no UTR / payment-reference column in this export, so
payment_ref is left null -- if Finance's export ever grows one, wire it
up in row_payment_ref() below.

CREDENTIALS
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  (same as every other sync script here -- set as real environment
  variables, or dropped in scripts/.env for a local run; see
  last_mile_lib/envfile.py)

Usage:
    python scripts/sync_payment_status_manual.py "<path to Payout File .xlsx>" [--dry-run]
"""
import datetime
import os
import re
import sys

import openpyxl
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from last_mile_lib import envfile               # noqa: E402

REQUEST_TIMEOUT = 30

# Stripped off the end of a vendor name before comparing the payout
# file's "Vendor Name" against profiles.vendor_name -- longest first, so
# "PRIVATE LIMITED" matches before a shorter suffix could partially eat it.
LEGAL_SUFFIXES = sorted(
    ["PRIVATE LIMITED", "PVT LTD", "PVT. LTD.", "LIMITED", "LTD", "LLP", "CO LTD"],
    key=len, reverse=True,
)


def normalize_vendor_name(name):
    n = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    n = re.sub(r"\s+", " ", n).strip()
    for suf in LEGAL_SUFFIXES:
        if n.endswith(" " + suf):
            return n[: -(len(suf) + 1)].strip()
    return n


def normalize_invoice_number(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def num(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def parse_date(v):
    """Payout file dates are 'DD-MM-YYYY' strings (or already datetimes if
    Excel stored them as a real date cell) -- normalize either to ISO."""
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    try:
        return datetime.datetime.strptime(s, "%d-%m-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def row_payment_status(row):
    """'paid' / 'pending' / None (unknown -- row left untouched)."""
    unpaid = num(row.get("Unpaid Amount"))
    if unpaid is not None:
        return "paid" if abs(unpaid) < 1 else "pending"
    paid = num(row.get("Invoice Amount Paid"))
    amt = num(row.get("Invoice Amount"))
    if paid is not None and amt is not None:
        return "paid" if abs(amt - paid) < 1 else "pending"
    return None


def row_payment_ref(row):
    return None  # no UTR/reference column in this export -- see module docstring


def supabase_config():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY environment variables.")
    return url.rstrip("/"), key


def fetch_vendor_map(supabase_url, key):
    """{normalized core vendor name: vendor_code} for every vendor login."""
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(
        f"{supabase_url}/rest/v1/profiles", headers=h,
        params={"role": "eq.vendor", "select": "vendor_code,vendor_name"},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    out = {}
    for row in r.json():
        code, name = row.get("vendor_code"), row.get("vendor_name")
        if code and name:
            out[normalize_vendor_name(name)] = code
    return out


def fetch_invoice_uploads(supabase_url, key):
    """{(vendor_code, normalized invoice number): [upload id, ...]} --
    only rows the AI match check actually got an invoice number out of."""
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(
        f"{supabase_url}/rest/v1/po_invoice_uploads", headers=h,
        params={"select": "id,vendor_code,match_details", "limit": 10000},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    lookup, unmatched_uploads = {}, 0
    for row in rows:
        inv = normalize_invoice_number((row.get("match_details") or {}).get("extracted", {}).get("invoice_number"))
        if not inv:
            unmatched_uploads += 1
            continue
        key_ = (row["vendor_code"], inv)
        lookup.setdefault(key_, []).append(row["id"])
    return lookup, len(rows), unmatched_uploads


def read_payout_rows(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows_iter)]
    for values in rows_iter:
        if values is None or all(v is None for v in values):
            continue
        yield dict(zip(header, values))


def build_updates(payout_path, vendor_map, upload_lookup):
    """Returns (updates: {upload_id: {payment_status, payment_date,
    payment_ref}}, stats dict)."""
    updates = {}
    stats = {"total_rows": 0, "portal_vendor_rows": 0, "matched_uploads": 0,
              "unknown_status_rows": 0, "portal_vendors_seen": set()}
    conflicts = {}  # upload_id -> set of statuses seen, for a same-run duplicate warning

    for row in read_payout_rows(payout_path):
        stats["total_rows"] += 1
        vendor_code = vendor_map.get(normalize_vendor_name(row.get("Vendor Name")))
        if not vendor_code:
            continue
        stats["portal_vendor_rows"] += 1
        stats["portal_vendors_seen"].add(vendor_code)

        inv = normalize_invoice_number(row.get("Invoice Num"))
        if not inv:
            continue
        upload_ids = upload_lookup.get((vendor_code, inv))
        if not upload_ids:
            continue

        status = row_payment_status(row)
        if status is None:
            stats["unknown_status_rows"] += 1
            continue

        payload = {
            "payment_status": status,
            "payment_date": parse_date(row.get("Payment Date")),
            "payment_ref": row_payment_ref(row),
        }
        for uid in upload_ids:
            if uid in updates and updates[uid]["payment_status"] != status:
                conflicts.setdefault(uid, set()).update({updates[uid]["payment_status"], status})
            updates[uid] = payload  # last row for this invoice number wins
            stats["matched_uploads"] += 1

    stats["conflicts"] = conflicts
    return updates, stats


def apply_updates(supabase_url, key, updates, dry_run):
    if not updates:
        return 0
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    synced_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    applied = 0
    for uid, payload in updates.items():
        body = {**payload, "payment_synced_at": synced_at}
        if dry_run:
            print(f"  [dry-run] upload {uid}: {body}")
            applied += 1
            continue
        r = requests.patch(
            f"{supabase_url}/rest/v1/po_invoice_uploads", headers=h,
            params={"id": f"eq.{uid}"}, json=body, timeout=REQUEST_TIMEOUT,
        )
        if not r.ok:
            print(f"  FAILED upload {uid} ({r.status_code}): {r.text[:300]}")
            continue
        applied += 1
    return applied


def main():
    envfile.load_local_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv[1:]
    if not args:
        sys.exit("Usage: python scripts/sync_payment_status_manual.py \"<path to Payout File .xlsx>\" [--dry-run]")
    payout_path = args[0]
    if not os.path.isfile(payout_path):
        sys.exit(f"File not found: {payout_path}")

    supabase_url, supabase_key = supabase_config()
    vendor_map = fetch_vendor_map(supabase_url, supabase_key)
    if not vendor_map:
        sys.exit("No vendor logins found in profiles (role='vendor') -- aborting.")

    upload_lookup, total_uploads, uploads_without_extracted_invoice = fetch_invoice_uploads(supabase_url, supabase_key)

    updates, stats = build_updates(payout_path, vendor_map, upload_lookup)
    applied = apply_updates(supabase_url, supabase_key, updates, dry_run)

    print(f"{'[DRY RUN] ' if dry_run else ''}Payout file: {payout_path}")
    print(f"  {stats['total_rows']} row(s) read; {stats['portal_vendor_rows']} belong to this portal's "
          f"{len(stats['portal_vendors_seen'])} vendor(s) ({', '.join(sorted(stats['portal_vendors_seen']))})")
    print(f"  {total_uploads} po_invoice_uploads row(s) total "
          f"({uploads_without_extracted_invoice} with no AI-extracted invoice number yet -- can't be matched)")
    print(f"  {applied} po_invoice_uploads row(s) updated with a payment status")
    if stats["unknown_status_rows"]:
        print(f"  {stats['unknown_status_rows']} matched row(s) had neither Unpaid Amount nor "
              f"Invoice Amount Paid -- left untouched")
    if stats["conflicts"]:
        print(f"  WARNING: {len(stats['conflicts'])} upload(s) matched more than one payout row with "
              f"conflicting status within this same file -- kept the last one encountered:")
        for uid, seen in stats["conflicts"].items():
            print(f"    upload {uid}: saw {sorted(seen)}")


if __name__ == "__main__":
    main()
