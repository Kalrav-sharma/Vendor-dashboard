#!/usr/bin/env python3
"""
Dispatch Planning email automation, via Resend's API. Three kinds of email:

  - "new_po": one per genuinely new purchase order, queued by the
    queue_new_po_email() Postgres trigger (schema.sql) the moment
    sync_to_supabase.py's Uniware sync inserts it -- never on a resync
    update of an existing PO, and never for a historical PO discovered by
    a new-vendor backfill (the trigger itself excludes anything older
    than 4 days). Tells the vendor to log in, download the PO, and
    provide estimated dispatch date/qty per SKU within 7 days.

  - "next_dispatch": one per SKU, queued by the confirm_dispatched()
    Postgres function (schema.sql) when Operations clicks "Dispatched" on
    a Dispatch Planning row and that SKU still has pending_quantity > 0
    (the function has already cleared its estimate back to null by the
    time this sends). Tells the vendor to provide a fresh estimate for the
    remaining balance.

  - "reminder": computed fresh every run (nothing pre-queues these) -- for
    any po_items row that's still pending (pending_quantity > 0) and still
    missing estimated_dispatch_date or estimated_dispatch_qty, once 7+
    days have passed since whichever is more recent of that PO's "new_po"
    email or that SPECIFIC SKU's own later "next_dispatch" email (a SKU
    that already went through one dispatch-and-reset cycle gets its own
    fresh 7-day clock, separate from the rest of the PO). Sends ONE email
    per PO per calendar day (IST) listing every SKU due for a nudge that
    day, repeating daily for as long as it stays incomplete (Kalrav's
    explicit spec: first reminder on the 8th day, daily after that), and
    stopping once a SKU is filled in OR its pending_quantity reaches 0
    (nothing left to dispatch, so nothing left to remind about).

Runs on a schedule (GitHub Actions) + supports workflow_dispatch for a
manual test run. Credentials/config, as GitHub Actions repo secrets:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY  -- already set for other syncs
  RESEND_API_KEY                            -- Resend API key
  RESEND_FROM_EMAIL                         -- verified sender, e.g.
                                              "Native Vendor Portal <noreply@yourdomain.com>"
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))
REQUEST_TIMEOUT = 30
RESEND_API_URL = "https://api.resend.com/emails"
PORTAL_URL = "https://kalrav-sharma.github.io/Vendor-dashboard/vendor.html"
REMINDER_DELAY_DAYS = 7  # first reminder fires once this many days have passed since the anchor email was sent


def supabase_config():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY environment variables.")
    return url.rstrip("/"), key


def resend_config():
    key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("RESEND_FROM_EMAIL")
    if not key or not from_email:
        sys.exit("Missing RESEND_API_KEY / RESEND_FROM_EMAIL environment variables.")
    return key, from_email


def sb_headers(key):
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def sb_get(session, url, key, path, params):
    r = session.get(f"{url}/rest/v1/{path}", headers=sb_headers(key), params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def sb_patch(session, url, key, path, params, body):
    r = session.patch(f"{url}/rest/v1/{path}", headers=sb_headers(key), params=params, json=body, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        print(f"WARN: patch {path} failed ({r.status_code}): {r.text[:300]}", file=sys.stderr)
    return r.ok


def sb_post(session, url, key, path, body):
    r = session.post(f"{url}/rest/v1/{path}", headers=sb_headers(key), json=body, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        print(f"WARN: insert {path} failed ({r.status_code}): {r.text[:300]}", file=sys.stderr)
    return r.ok


def send_email(session, resend_key, from_email, to_email, subject, html):
    r = session.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
        json={"from": from_email, "to": [to_email], "subject": subject, "html": html},
        timeout=REQUEST_TIMEOUT,
    )
    if not r.ok:
        return False, r.text[:300]
    return True, None


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def vendor_email_map(session, sb_url, sb_key):
    rows = sb_get(session, sb_url, sb_key, "profiles",
                  {"role": "eq.vendor", "select": "vendor_code,email,vendor_name"})
    out = {}
    for row in rows:
        if row.get("vendor_code") and row.get("email"):
            out[row["vendor_code"]] = {"email": row["email"], "name": row.get("vendor_name") or row["vendor_code"]}
    return out


def new_po_email_html(po_code, vendor_name):
    return (
        f"<p>Hi {vendor_name},</p>"
        f"<p>A new purchase order <b>{po_code}</b> has been created for you on the Native/UC vendor portal.</p>"
        f'<p>Please log in to <a href="{PORTAL_URL}">the portal</a> to download the PO copy, and provide the '
        f"<b>estimated dispatch date</b> and <b>estimated dispatch quantity</b> for each SKU on this PO within "
        f"<b>7 days</b> of this email.</p>"
    )


def next_dispatch_email_html(po_code, vendor_name, item):
    pending = item.get("pending_quantity")
    pending_line = f"There are still <b>{pending:g}</b> unit(s) pending" if pending else "There may still be units pending"
    return (
        f"<p>Hi {vendor_name},</p>"
        f"<p>Thanks -- the estimated dispatch for <b>{item['item_sku']}</b> "
        f"({item.get('item_name') or ''}) on purchase order <b>{po_code}</b> has been confirmed.</p>"
        f"<p>{pending_line} on this SKU. Please log in to <a href=\"{PORTAL_URL}\">the portal</a> and provide a "
        f"new estimated dispatch date and quantity for the remaining balance.</p>"
    )


def reminder_email_html(po_code, vendor_name, pending_items):
    rows = "".join(
        f"<tr><td>{it['item_sku']}</td><td>{it.get('item_name') or ''}</td></tr>"
        for it in pending_items
    )
    return (
        f"<p>Hi {vendor_name},</p>"
        f"<p>Purchase order <b>{po_code}</b> still needs an estimated dispatch date and quantity for the "
        f"following SKU(s):</p>"
        f'<table border="1" cellpadding="6" cellspacing="0"><tr><th>SKU</th><th>Item</th></tr>{rows}</table>'
        f'<p>Please log in to <a href="{PORTAL_URL}">the portal</a> and update these as soon as possible.</p>'
    )


def process_initial_events(session, sb_url, sb_key, resend_key, from_email, vendor_map):
    """Sends 'new_po' and 'next_dispatch' events whose sent_at is still
    null -- each uses its own template, but both are otherwise handled the
    same way (send, mark sent_at or error)."""
    events = sb_get(session, sb_url, sb_key, "po_email_events", {
        "event_type": "in.(new_po,next_dispatch)", "sent_at": "is.null",
        "select": "id,po_code,vendor_code,event_type,item_sku",
    })
    sent = 0
    for ev in events:
        vendor = vendor_map.get(ev["vendor_code"])
        if not vendor:
            print(f"WARN: no vendor login/email found for vendor_code={ev['vendor_code']!r} "
                  f"(po_code={ev['po_code']}) -- skipping, will retry next run", file=sys.stderr)
            continue

        if ev["event_type"] == "new_po":
            subject = f"New Purchase Order Created: {ev['po_code']}"
            html = new_po_email_html(ev["po_code"], vendor["name"])
        else:  # next_dispatch
            item_rows = sb_get(session, sb_url, sb_key, "po_items", {
                "po_code": f"eq.{ev['po_code']}", "item_sku": f"eq.{ev['item_sku']}",
                "select": "item_sku,item_name,pending_quantity", "limit": "1",
            })
            item = item_rows[0] if item_rows else {
                "item_sku": ev["item_sku"], "item_name": "", "pending_quantity": None,
            }
            subject = f"Next Dispatch Needed: {ev['po_code']} / {ev['item_sku']}"
            html = next_dispatch_email_html(ev["po_code"], vendor["name"], item)

        ok, err = send_email(session, resend_key, from_email, vendor["email"], subject, html)
        if ok:
            sb_patch(session, sb_url, sb_key, "po_email_events", {"id": f"eq.{ev['id']}"},
                     {"sent_at": datetime.now(timezone.utc).isoformat()})
            sent += 1
        else:
            sb_patch(session, sb_url, sb_key, "po_email_events", {"id": f"eq.{ev['id']}"}, {"error": err})
    return sent


def process_reminders(session, sb_url, sb_key, resend_key, from_email, vendor_map):
    # Still-pending SKUs (real dispatch remaining, per Uniware) that still
    # need an estimate.
    pending_items = sb_get(session, sb_url, sb_key, "po_items", {
        "pending_quantity": "gt.0",
        "or": "(estimated_dispatch_date.is.null,estimated_dispatch_qty.is.null)",
        "select": "po_code,item_sku,item_name,vendor_code,pending_quantity",
    })
    if not pending_items:
        return 0

    po_codes = list({it["po_code"] for it in pending_items})
    anchor_events = sb_get(session, sb_url, sb_key, "po_email_events", {
        "po_code": f"in.({','.join(po_codes)})",
        "event_type": "in.(new_po,next_dispatch)", "sent_at": "not.is.null",
        "select": "po_code,item_sku,event_type,sent_at",
    })
    po_level_anchor = {}   # po_code -> latest 'new_po' sent_at (applies to every SKU on that PO by default)
    sku_level_anchor = {}  # (po_code, item_sku) -> latest 'next_dispatch' sent_at (overrides the PO-level one)
    for ev in anchor_events:
        sent_at = parse_ts(ev["sent_at"])
        if ev["event_type"] == "new_po":
            key = ev["po_code"]
            existing = po_level_anchor.get(key)
            po_level_anchor[key] = sent_at if existing is None else max(sent_at, existing)
        elif ev["event_type"] == "next_dispatch" and ev.get("item_sku"):
            key = (ev["po_code"], ev["item_sku"])
            existing = sku_level_anchor.get(key)
            sku_level_anchor[key] = sent_at if existing is None else max(sent_at, existing)

    cutoff = datetime.now(timezone.utc) - timedelta(days=REMINDER_DELAY_DAYS)

    # "Today" as an IST calendar day, expressed as the UTC instant of IST
    # midnight -- comparing a timestamptz column against a bare date string
    # would silently use UTC midnight as the boundary instead, off by 5:30.
    now_ist = datetime.now(IST)
    midnight_ist = datetime(now_ist.year, now_ist.month, now_ist.day, tzinfo=IST)
    today_ist_start_utc = midnight_ist.astimezone(timezone.utc).isoformat()

    already_reminded_today = sb_get(session, sb_url, sb_key, "po_email_events", {
        "event_type": "eq.reminder", "queued_at": f"gte.{today_ist_start_utc}", "select": "po_code",
    })
    reminded_today = {r["po_code"] for r in already_reminded_today}

    due_by_po = {}
    for it in pending_items:
        key = (it["po_code"], it["item_sku"])
        anchor = sku_level_anchor.get(key) or po_level_anchor.get(it["po_code"])
        if not anchor or anchor > cutoff:
            continue  # no notification sent yet, or not 7 days old yet
        if it["po_code"] in reminded_today:
            continue
        due_by_po.setdefault(it["po_code"], []).append(it)

    sent = 0
    for po_code, items in due_by_po.items():
        vendor_code = items[0]["vendor_code"]
        vendor = vendor_map.get(vendor_code)
        if not vendor:
            continue
        ok, err = send_email(session, resend_key, from_email, vendor["email"],
                              f"Reminder: Update Dispatch Details for {po_code}",
                              reminder_email_html(po_code, vendor["name"], items))
        sb_post(session, sb_url, sb_key, "po_email_events", [{
            "po_code": po_code, "vendor_code": vendor_code, "event_type": "reminder",
            "sent_at": datetime.now(timezone.utc).isoformat() if ok else None,
            "error": None if ok else err,
        }])
        if ok:
            sent += 1
    return sent


def main():
    sb_url, sb_key = supabase_config()
    resend_key, from_email = resend_config()
    session = requests.Session()
    vendor_map = vendor_email_map(session, sb_url, sb_key)

    initial_sent = process_initial_events(session, sb_url, sb_key, resend_key, from_email, vendor_map)
    reminder_sent = process_reminders(session, sb_url, sb_key, resend_key, from_email, vendor_map)
    print(f"Sent {initial_sent} new-PO/next-dispatch email(s), {reminder_sent} reminder email(s).")


if __name__ == "__main__":
    main()
