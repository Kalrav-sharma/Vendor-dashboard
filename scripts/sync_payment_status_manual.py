#!/usr/bin/env python3
"""
Populates payment_status / payment_date / payment_ref / payment_synced_at
on TWO tables from Finance's weekly "Payout File" Oracle Fusion AP export
(an .xlsx workbook, one row per invoice line, covering every UC vendor --
not just this portal's):

  po_invoice_uploads -- matched on (vendor_code, invoice number), where
      the invoice number comes from the AI match check's OCR read of a
      vendor-uploaded PDF. Only a PO with an uploaded invoice can ever
      get a row here.
  purchase_orders    -- matched on (vendor_code, po_code) instead, via
      the payout file's "Poms Number" column, which is on every payout
      row regardless of whether the vendor ever uploaded anything. This
      is what lets a PO Finance has already booked or paid -- but that
      still has ZERO invoice uploaded through the portal -- show a real
      status instead of a bare "needs invoice" indistinguishable from
      one nobody has done anything about (measured 2026-09-30: 6 of
      GELTRON's 11 POs, all fully received, zero uploads, all sitting
      silently in Finance's ledger).

Both target columns already exist in schema.sql (see the comments above
them) but nothing wrote to purchase_orders' side before this -- the
originally-planned Jarvis sync for po_invoice_uploads' side was never
built either. This script is the substitute for both: same write path
(service_role, no INSERT/UPDATE policy for authenticated on either
table), just a manual file drop instead of a live API pull.

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
number), against the payout file's "Invoice Num" column. purchase_orders
rows are matched on (vendor_code, po_code) against the payout file's
"Poms Number" column instead -- no AI check needed, since po_code is
already on record the moment the PO itself synced from Uniware. Vendor
identity, for both, comes from comparing the payout file's "Vendor Name"
against this portal's profiles.vendor_name (role='vendor'), after
stripping the legal-entity suffix ("Pvt Ltd" vs "Private Limited" etc. --
Finance's Oracle export and our profiles table don't always spell the
same vendor's name the same way).

There is no UTR / payment-reference column in this export, so
payment_ref is left null -- if Finance's export ever grows one, wire it
up in row_payment_ref() below.

If the same invoice number shows up on more than one row in one file
(a partial payment split across lines, a correction, etc.), only the
most recent/authoritative one is used -- see row_recency_key(). An
upload already marked 'paid' from a previous run is also never reverted
to 'pending' by a later run, in case an older file ever gets run out of
order (see build_updates()).

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


#: Placeholder values Finance's export uses for "not applicable here" --
#: seen literally as a "Poms Number" value on rows with no real PO behind
#: them (a prepayment, an adjustment entry, etc.). Checked case-insensitively.
BLANK_PLACEHOLDERS = {"NA", "N/A", "-", "NULL", "NONE"}


def normalize_invoice_number(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.upper() in BLANK_PLACEHOLDERS:
        return None
    return s


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


def row_recency_key(row):
    """Sort key for picking the most authoritative row when the same
    invoice number appears more than once in one payout file (a partial
    payment split across lines, a correction, etc.) -- higher sorts as
    more recent/authoritative. 'paid' always outranks 'pending' (a
    ledger's paid state doesn't get less true), and within a status, the
    row with the latest date wins."""
    status_rank = {"paid": 1, "pending": 0}.get(row_payment_status(row), -1)
    pay_date = parse_date(row.get("Payment Date")) or ""
    acct_date = parse_date(row.get("Invoice Accounting Date")) or ""
    return (status_rank, pay_date, acct_date)


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
    """({(vendor_code, normalized invoice number): [upload id, ...]},
    {upload id: current payment_status}) -- only rows the AI match check
    actually got an invoice number out of are in the first lookup; the
    second covers every upload, used to guard against a stale re-run
    silently downgrading an already-paid row (see build_updates)."""
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(
        f"{supabase_url}/rest/v1/po_invoice_uploads", headers=h,
        params={"select": "id,vendor_code,match_details,payment_status", "limit": 10000},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    lookup, current_status, unmatched_uploads = {}, {}, 0
    for row in rows:
        current_status[row["id"]] = row.get("payment_status")
        inv = normalize_invoice_number((row.get("match_details") or {}).get("extracted", {}).get("invoice_number"))
        if not inv:
            unmatched_uploads += 1
            continue
        key_ = (row["vendor_code"], inv)
        lookup.setdefault(key_, []).append(row["id"])
    return lookup, current_status, len(rows), unmatched_uploads


def fetch_po_payment_status(supabase_url, key):
    """{(vendor_code, po_code): current payment_status} for every PO on
    file -- po_code is purchase_orders' primary key, so unlike uploads
    this is a 1:1 lookup, no id indirection needed. Same downgrade-guard
    purpose as fetch_invoice_uploads' current_status."""
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, frm = [], 0
    while True:
        r = requests.get(
            f"{supabase_url}/rest/v1/purchase_orders",
            headers={**h, "Range": f"{frm}-{frm + 999}"},
            params={"select": "po_code,vendor_code,payment_status"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < 1000:
            break
        frm += 1000
    return {(row["vendor_code"], row["po_code"]): row.get("payment_status") for row in rows}


#: How far down the sheet to look for the real header row -- Oracle's raw
#: "UC Payout Report_Layout" export (Finance's files from 2026-10-05 on)
#: puts report-parameter rows ("Business Unit:", "From Date:", "To Date:")
#: above it, where older hand-cleaned files had the header on row 1.
HEADER_SEARCH_ROWS = 20


def read_payout_rows(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    header = None
    for _, values in zip(range(HEADER_SEARCH_ROWS), rows_iter):
        cells = [str(h).strip() if h is not None else "" for h in (values or ())]
        if "Vendor Name" in cells and "Invoice Num" in cells:
            header = cells
            break
    if header is None:
        # Previously this silently treated row 1 as the header, matched zero
        # vendors and "succeeded" having updated nothing -- fail loudly instead.
        sys.exit(f"No header row with 'Vendor Name' and 'Invoice Num' in the first "
                 f"{HEADER_SEARCH_ROWS} rows of {path} -- has the export layout changed?")
    for values in rows_iter:
        if values is None or all(v is None for v in values):
            continue
        yield dict(zip(header, values))


def build_updates(payout_path, vendor_map, upload_lookup, current_status, po_current_status):
    """Returns (upload_updates: {upload_id: {payment_status, payment_date,
    payment_ref}}, po_updates: {(vendor_code, po_code): {...same...}},
    stats dict). One scan of the payout file drives both -- it's the same
    rows either way, just grouped by two different keys (invoice number
    for uploads, "Poms Number"/po_code for purchase_orders).

    Two safeguards around "which status is actually current", applied to
    BOTH groupings independently:
      - The same key on more than one row IN THIS FILE (a partial payment
        split across lines, a correction, etc.) -- only the most recent/
        authoritative one (row_recency_key) is used, not just whichever
        happened to come last while reading the sheet.
      - A row already marked 'paid' from a PREVIOUS run never gets
        silently downgraded back to 'pending' by THIS run -- that would
        only happen from a stale/out-of-order file (payout files are
        cumulative, so a properly-ordered run never sees this), and
        Oracle's paid state doesn't become less true later.
    """
    by_invoice = {}  # (vendor_code, invoice_number) -> payout row, most-recent so far
    by_po = {}        # (vendor_code, po_code) -> payout row, most-recent so far
    stats = {"total_rows": 0, "portal_vendor_rows": 0, "matched_uploads": 0,
              "matched_pos": 0, "unknown_status_rows": 0, "portal_vendors_seen": set(),
              "duplicate_invoices": 0, "duplicate_pos": 0}

    for row in read_payout_rows(payout_path):
        stats["total_rows"] += 1
        vendor_code = vendor_map.get(normalize_vendor_name(row.get("Vendor Name")))
        if not vendor_code:
            continue
        stats["portal_vendor_rows"] += 1
        stats["portal_vendors_seen"].add(vendor_code)

        inv = normalize_invoice_number(row.get("Invoice Num"))
        if inv:
            key_ = (vendor_code, inv)
            if key_ in by_invoice:
                stats["duplicate_invoices"] += 1
            if key_ not in by_invoice or row_recency_key(row) > row_recency_key(by_invoice[key_]):
                by_invoice[key_] = row

        po_code = normalize_invoice_number(row.get("Poms Number"))  # same "trim, treat blank as absent" rule
        if po_code:
            key_ = (vendor_code, po_code)
            if key_ in by_po:
                stats["duplicate_pos"] += 1
            if key_ not in by_po or row_recency_key(row) > row_recency_key(by_po[key_]):
                by_po[key_] = row

    upload_updates, downgrades_skipped = {}, 0
    for key_, row in by_invoice.items():
        upload_ids = upload_lookup.get(key_)
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
            if status == "pending" and current_status.get(uid) == "paid":
                downgrades_skipped += 1
                continue
            upload_updates[uid] = payload
            stats["matched_uploads"] += 1

    po_updates, po_downgrades_skipped, pos_not_on_portal = {}, 0, 0
    for key_, row in by_po.items():
        if key_ not in po_current_status:
            # A portal vendor's PO that never synced here (created before the
            # Aug 1 sync window, or at a facility we don't pull) -- PATCHing it
            # matches zero rows, and these were ~3/4 of all PO writes on
            # 2026-10-05 (716 of 933), so skip them rather than spend a round
            # trip each on a no-op.
            pos_not_on_portal += 1
            continue
        status = row_payment_status(row)
        if status is None:
            continue  # already counted in unknown_status_rows above when this row also had an invoice number
        if status == "pending" and po_current_status.get(key_) == "paid":
            po_downgrades_skipped += 1
            continue
        po_updates[key_] = {
            "payment_status": status,
            "payment_date": parse_date(row.get("Payment Date")),
            "payment_ref": row_payment_ref(row),
        }
        stats["matched_pos"] += 1

    stats["downgrades_skipped"] = downgrades_skipped
    stats["po_downgrades_skipped"] = po_downgrades_skipped
    stats["pos_not_on_portal"] = pos_not_on_portal
    return upload_updates, po_updates, stats


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


def apply_po_updates(supabase_url, key, po_updates, dry_run):
    if not po_updates:
        return 0
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    synced_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    applied = 0
    for (vendor_code, po_code), payload in po_updates.items():
        body = {**payload, "payment_synced_at": synced_at}
        if dry_run:
            print(f"  [dry-run] PO {po_code}: {body}")
            applied += 1
            continue
        r = requests.patch(
            f"{supabase_url}/rest/v1/purchase_orders", headers=h,
            params={"po_code": f"eq.{po_code}", "vendor_code": f"eq.{vendor_code}"},
            json=body, timeout=REQUEST_TIMEOUT,
        )
        if not r.ok:
            print(f"  FAILED PO {po_code} ({r.status_code}): {r.text[:300]}")
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

    upload_lookup, current_status, total_uploads, uploads_without_extracted_invoice = \
        fetch_invoice_uploads(supabase_url, supabase_key)

    po_current_status = fetch_po_payment_status(supabase_url, supabase_key)

    upload_updates, po_updates, stats = build_updates(
        payout_path, vendor_map, upload_lookup, current_status, po_current_status)
    applied = apply_updates(supabase_url, supabase_key, upload_updates, dry_run)
    po_applied = apply_po_updates(supabase_url, supabase_key, po_updates, dry_run)

    print(f"{'[DRY RUN] ' if dry_run else ''}Payout file: {payout_path}")
    print(f"  {stats['total_rows']} row(s) read; {stats['portal_vendor_rows']} belong to this portal's "
          f"{len(stats['portal_vendors_seen'])} vendor(s) ({', '.join(sorted(stats['portal_vendors_seen']))})")
    print(f"  {total_uploads} po_invoice_uploads row(s) total "
          f"({uploads_without_extracted_invoice} with no AI-extracted invoice number yet -- can't be matched)")
    print(f"  {applied} po_invoice_uploads row(s) updated with a payment status")
    print(f"  {po_applied} purchase_orders row(s) updated with a payment status (matched by PO code, "
          f"whether or not an invoice was ever uploaded)")
    if stats["pos_not_on_portal"]:
        print(f"  {stats['pos_not_on_portal']} PO code(s) in the file belong to a portal vendor but aren't in "
              f"purchase_orders (older than the sync window, etc.) -- skipped")
    if stats["duplicate_invoices"]:
        print(f"  {stats['duplicate_invoices']} invoice number(s) appeared on more than one row in this file -- "
              f"used the most recent/authoritative one for each")
    if stats["duplicate_pos"]:
        print(f"  {stats['duplicate_pos']} PO code(s) appeared on more than one row in this file -- "
              f"used the most recent/authoritative one for each")
    if stats["unknown_status_rows"]:
        print(f"  {stats['unknown_status_rows']} matched row(s) had neither Unpaid Amount nor "
              f"Invoice Amount Paid -- left untouched")
    if stats["downgrades_skipped"]:
        print(f"  {stats['downgrades_skipped']} upload(s) already marked 'paid' were NOT reverted to 'pending' "
              f"by this file -- likely a stale/out-of-order payout file; re-check if unexpected")
    if stats["po_downgrades_skipped"]:
        print(f"  {stats['po_downgrades_skipped']} PO(s) already marked 'paid' were NOT reverted to 'pending' "
              f"by this file -- likely a stale/out-of-order payout file; re-check if unexpected")


if __name__ == "__main__":
    main()
