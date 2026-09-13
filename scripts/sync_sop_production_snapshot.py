#!/usr/bin/env python3
"""
S&OP: Production Plan tab -- daily "planned" freeze.

The "Day wise trackr" tab's Combined/Ronch/Amber Actual Production cells
hold a PLACEHOLDER PLAN VALUE right up until that calendar date actually
happens, then get silently overwritten with the true actual (a real,
documented sheet behavior -- not something this script works around
defensively for no reason; see sync_sop_production.py's docstring). So
the only way to know what was actually PLANNED for a given date, after
that date has passed, is to have captured today's Actual Production cell
value BEFORE today happens -- i.e. run this once daily, early morning,
before production entries for the day start landing.

Runs via the "Sync S&OP production snapshot" GitHub Actions workflow,
scheduled at 04:25 UTC (09:55 AM IST) -- comfortably before the day's own
entries typically start. Reads ONLY today's row, for all three facilities
(COMBINED/RONCH/AMBER), and inserts into production_plan_snapshots with
ON CONFLICT DO NOTHING (never overwrites an existing snapshot, so a
second accidental run for the same day is a harmless no-op, not a
corruption risk).

Credentials: GOOGLE_SERVICE_ACCOUNT_JSON, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY (all already provisioned, no new secrets).
"""
import datetime
import sys
import zoneinfo

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import SKUS, WH_CHANNEL_SKU_ID, get_access_token, get_values, supabase_config, upsert  # noqa: E402
from sync_sop_production import parse_daily_production  # noqa: E402


def main():
    supabase_url, supabase_key = supabase_config()
    token = get_access_token()

    today_ist = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Kolkata")).date().isoformat()

    rows = get_values(token, WH_CHANNEL_SKU_ID, "'Day wise trackr'")
    actual_by_date = parse_daily_production(rows)

    if today_ist not in actual_by_date:
        sys.exit(f"Today ({today_ist}) has no row yet in Day wise trackr's Actual Production "
                  f"blocks -- aborting without writing a partial/empty snapshot.")

    today_entry = actual_by_date[today_ist]
    out_rows = []
    for facility in ("COMBINED", "RONCH", "AMBER"):
        for sku in SKUS:
            out_rows.append({
                "snapshot_date": today_ist, "sku": sku, "facility": facility,
                "planned_qty": today_entry[facility].get(sku, 0.0),
            })

    upsert(supabase_url, supabase_key, "production_plan_snapshots", out_rows,
           "snapshot_date,sku,facility", ignore_duplicates=True)
    print(f"Snapshotted {len(out_rows)} planned-production row(s) for {today_ist} "
          f"(no-op for any (date, sku, facility) already captured).")


if __name__ == "__main__":
    main()
