#!/usr/bin/env python3
"""
Spares Projections (beta) step 1 of 2: upload the monthly demand forecast -> public.spares_forecast_demand.

The forecast needs Jarvis (VPN-gated), so it is built on the VPN laptop by the forecast_spares skill
(~/.claude/skills/forecast_spares/refresh_forecast.sh). This script then uploads that run's 5-month
SKU-total demand (vendor_extended_5m.csv) plus the Refresh / IK kit rules, so the projection job
(scripts/sync_spares_projection.py, GitHub Actions) can apply live stock to it without VPN access.

  M1 = the month after the forecast's as-of date (extended_forecast.py: fc5[i] = addm(asof month, i+1)).
  The CSV only carries month labels, so the as-of date is taken from the file's mtime and the labels are
  cross-checked against it -- a stale or mismatched file aborts instead of uploading wrong months.
  demand_driver: 'refresh' for refresh_bom.csv children (renewal-driven); 'ik' for ik_bom.csv components
  only once data/ik_onboarding.csv has rows (until then the forecast treats them as order-driven);
  else 'order'. bom_vendor is the vendor fallback from those BOM files.

Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY. Skill dir: FORECAST_SPARES_DIR or the default below.

  python3 scripts/sync_spares_forecast_demand.py            # upload (wholesale replace)
  python3 scripts/sync_spares_forecast_demand.py --dry-run  # read + print, no write
  --asof YYYY-MM-DD overrides the file-mtime as-of date.
"""
import calendar
import csv
import datetime
import os
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import replace_by_filter, supabase_config  # noqa: E402

SKILL_DIR = os.environ.get("FORECAST_SPARES_DIR", os.path.expanduser("~/.claude/skills/forecast_spares"))

# Beta scope: discontinued but still showing demand; left out of the projections for now
# (owner call 2026-10-23). Compared case-insensitively.
IGNORED_SKUS = {"uc/native/tdssensor/4p", "uc/native/miniups24vdc"}


def addm(ym, n):
    y, m = ym
    t = y * 12 + (m - 1) + n
    return (t // 12, t % 12 + 1)


def read_csv(name):
    with open(os.path.join(SKILL_DIR, name), newline="") as f:
        return list(csv.DictReader(f))


def main():
    dry = "--dry-run" in sys.argv
    asof = None
    if "--asof" in sys.argv:
        asof = datetime.date.fromisoformat(sys.argv[sys.argv.index("--asof") + 1])
    src = os.path.join(SKILL_DIR, "vendor_extended_5m.csv")
    if asof is None:
        asof = datetime.date.fromtimestamp(os.path.getmtime(src))

    rows = read_csv("vendor_extended_5m.csv")
    labels = [c for c in rows[0].keys() if c not in ("ITEM_TYPE_SKU", "MOVER_CLASS")]
    months = [addm((asof.year, asof.month), i + 1) for i in range(5)]
    expect = [calendar.month_abbr[m].upper() for _, m in months]
    if labels != expect:
        sys.exit(f"Month labels {labels} don't match as-of {asof} (expected {expect}). "
                 f"Re-run the forecast, or pass --asof.")

    refresh = {r["CHILD_SKU"].strip().lower(): (r.get("VENDOR") or "").strip() for r in read_csv("refresh_bom.csv")}
    ik = {r["CHILD_SKU"].strip().lower(): (r.get("VENDOR") or "").strip() for r in read_csv("ik_bom.csv")}
    ik_live = bool(read_csv(os.path.join("data", "ik_onboarding.csv")))

    out, skipped = [], []
    for r in rows:
        sku = r["ITEM_TYPE_SKU"].strip()
        key = sku.lower()
        if key in IGNORED_SKUS:
            skipped.append(sku)
            continue
        driver = "refresh" if key in refresh else ("ik" if key in ik and ik_live else "order")
        bom_vendor = refresh.get(key) or ik.get(key) or None
        for (y, m), lab in zip(months, labels):
            out.append({
                "sku": sku, "month_start": f"{y}-{m:02d}-01", "demand": float(r[lab] or 0),
                "mover_class": r.get("MOVER_CLASS") or None, "demand_driver": driver,
                "bom_vendor": bom_vendor, "forecast_asof": asof.isoformat(),
            })

    n_sku = len(out) // 5
    by_driver = {}
    for o in out[::5]:
        by_driver[o["demand_driver"]] = by_driver.get(o["demand_driver"], 0) + 1
    print(f"as-of {asof} | months {months[0][0]}-{months[0][1]:02d}..{months[-1][0]}-{months[-1][1]:02d} ({'/'.join(labels)})")
    print(f"{n_sku} SKU(s), {len(out)} row(s); drivers {by_driver}; IK onboarding loaded: {ik_live}")
    for (y, m), lab in zip(months, labels):
        print(f"  {lab}: {sum(o['demand'] for o in out if o['month_start'] == f'{y}-{m:02d}-01'):,.0f} units")
    print(f"ignored SKUs: {skipped}")

    if dry:
        print("--dry-run: nothing written.")
        return
    url, key = supabase_config()
    replace_by_filter(url, key, "spares_forecast_demand", out, {"sku": "not.is.null"})
    print(f"Synced {len(out)} spares_forecast_demand row(s).")


if __name__ == "__main__":
    main()
