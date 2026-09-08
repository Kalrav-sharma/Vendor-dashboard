#!/usr/bin/env python3
"""
Dispatch Planning email automation, via Resend's API. Two kinds of email:

  - "new_po": one per genuinely new purchase order, queued by the
    queue_new_po_email() Postgres trigger (schema.sql) the moment
    sync_to_supabase.py's Uniware sync inserts it -- never on a resync
    update of an existing PO, and never for a historical PO discovered by
    a new-vendor backfill (the trigger itself excludes anything older
    than 4 days). Tells the vendor to log in, download the PO, and
    provide estimated dispatch date/qty per SKU within 7 days.

  - "reminder": computed fresh every run (nothing pre-queues these) -- for
    any PO whose "new_po" email was sent 7+ days ago and still has at
    least one po_items row missing estimated_dispatch_date or
    estimated_dispatch_qty, sends ONE reminder per calendar day (IST)
    listing every still-pending SKU on that PO. Naturally repeats daily
    for as long as it stays incomplete (Kalrav's explicit spec: first
    reminder on the 8th day, daily after that), and stops the run after
    every SKU is filled in, since the "still pending" condition becomes
    false.

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
REMINDER_DELAY_DAYS = 7  # first reminder fires once this many days have passed since "new_po" was sent


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


def process_new_po_events(session, sb_url, sb_key, resend_key, from_email, vendor_map):
    events = sb_get(session, sb_url, sb_key, "po_email_events", {
        "event_type": "eq.new_po", "sent_at": "is.null", "select": "id,po_code,vendor_code",
    })
    sent = 0
    for ev in events:
        vendor = vendor_map.get(ev["vendor_code"])
        if not vendor:
            print(f"WARN: no vendor login/email found for vendor_code={ev['vendor_code']!r} "
                  f"(po_code={ev['po_code']}) -- skipping, will retry next run", file=sys.stderr)
            continue
        ok, err = send_email(session, resend_key, from_email, vendor["email"],
                              f"New Purchase Order Created: {ev['po_code']}",
                              new_po_email_html(ev["po_code"], vendor["name"]))
        if ok:
            sb_patch(session, sb_url, sb_key, "po_email_events", {"id": f"eq.{ev['id']}"},
                     {"sent_at": datetime.now(timezone.utc).isoformat()})
            sent += 1
        else:
            sb_patch(session, sb_url, sb_key, "po_email_events", {"id": f"eq.{ev['id']}"}, {"error": err})
    return sent


def process_reminders(session, sb_url, sb_key, resend_key, from_email, vendor_map):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REMINDER_DELAY_DAYS)).isoformat()
    due_events = sb_get(session, sb_url, sb_key, "po_email_events", {
        "event_type": "eq.new_po", "sent_at": f"lte.{cutoff}", "select": "po_code,vendor_code",
    })
    if not due_events:
        return 0

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

    po_codes = list({e["po_code"] for e in due_events} - reminded_today)
    if not po_codes:
        return 0

    items = sb_get(session, sb_url, sb_key, "po_items", {
        "po_code": f"in.({','.join(po_codes)})",
        "select": "po_code,item_sku,item_name,estimated_dispatch_date,estimated_dispatch_qty",
    })
    pending_by_po = {}
    for it in items:
        if it.get("estimated_dispatch_date") is None or it.get("estimated_dispatch_qty") is None:
            pending_by_po.setdefault(it["po_code"], []).append(it)

    vendor_by_po = {e["po_code"]: e["vendor_code"] for e in due_events}
    sent = 0
    for po_code, pending_items in pending_by_po.items():
        vendor_code = vendor_by_po.get(po_code)
        vendor = vendor_map.get(vendor_code)
        if not vendor:
            continue
        ok, err = send_email(session, resend_key, from_email, vendor["email"],
                              f"Reminder: Update Dispatch Details for {po_code}",
                              reminder_email_html(po_code, vendor["name"], pending_items))
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

    new_sent = process_new_po_events(session, sb_url, sb_key, resend_key, from_email, vendor_map)
    reminder_sent = process_reminders(session, sb_url, sb_key, resend_key, from_email, vendor_map)
    print(f"Sent {new_sent} new-PO email(s), {reminder_sent} reminder email(s).")


if __name__ == "__main__":
    main()
