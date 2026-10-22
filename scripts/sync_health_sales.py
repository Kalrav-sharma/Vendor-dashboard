#!/usr/bin/env python3
"""
Health Card › 4.1 Net sales by SKU, month-over-month -> public.health_sales_monthly.

Same Jarvis SQL as the Native Freight & Dispatch console's Sales tab
(uc-inventory-dashboard/fetch_sales.py): Native RO net sales, India, net of
returns, from public.product_order__d2c_sales_creation_date__view, grouped by
sku_id x calendar month. A uc_return counts in the month it was recognised
(revenue_recognition_dt), everything else in the month it was created.

Jarvis is VPN-gated, so -- like spares_drr's sync_sla_portal.js -- this runs on
a VPN laptop, not GitHub Actions. Re-syncs the last MONTHS_BACK months
(current month to date included) and wholesale-replaces exactly that window, so
a late return landing in an earlier month is picked up and older history is
left alone.

Env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, and REDASH_API_KEY (falls back to
~/.uc-claude/redash-api-key). Behind UC's TLS proxy set REQUESTS_CA_BUNDLE to
the corp CA bundle.

  python3 scripts/sync_health_sales.py            # sync
  python3 scripts/sync_health_sales.py --dry-run  # query + print, no write
"""
import datetime
import os
import sys
import time

import requests

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import replace_by_filter, supabase_config  # noqa: E402

REDASH_URL = "https://jarvis.urbancompany.com"
DS_SNOWFLAKE = 20
MONTHS_BACK = 6  # the card shows 4; the extra 2 cover a later "show more months"

# sku_id -> display name, = fetch_sales.py's SKU_DISPLAY (the console's 9 RO SKUs).
SKU_DISPLAY = {
    "UC/NATIVE/12501/M0": "M0",
    "UC/12304/M1": "M1",
    "UC/Native/M1/12401": "M1 AS",
    "UC/Native/M1Pro/12601": "M1 Pro",
    "UC/12305/M2": "M2",
    "UC/Native/M2/12302": "M2 AS",
    "UC/Native/M2/12502": "M2 Pro",
    "UC/Native/M3/12602": "M3",
    "UC/Native/M3Pro/12603": "M3 Pro",
}

EVENT_TS = "CASE WHEN revenue_status = 'uc_return' THEN revenue_recognition_dt ELSE root_created_at END"


def build_sql(start, end):
    skus = ", ".join(f"'{s}'" for s in SKU_DISPLAY)
    return f"""SELECT sku_id,
       DATE_TRUNC('MONTH', {EVENT_TS}) AS order_month,
       SUM(CASE WHEN (return_adj_sku = 0 OR return_adj_sku IS NULL) THEN quantity
                ELSE quantity * (1 - return_adj_sku) END) AS net_sales
FROM public.product_order__d2c_sales_creation_date__view
WHERE {EVENT_TS} >= '{start} 00:00:00.000000'
  AND {EVENT_TS} < '{end} 00:00:00.000000'
  AND reporting_category_new IN ('RO Purchase')
  AND reporting_supercategory_new IN ('Native')
  AND sku_id IN ({skus})
  AND country IN ('India')
GROUP BY sku_id, DATE_TRUNC('MONTH', {EVENT_TS})"""


def redash_key():
    key = os.environ.get("REDASH_API_KEY")
    if not key:
        path = os.path.expanduser("~/.uc-claude/redash-api-key")
        if os.path.exists(path):
            key = open(path).read().strip()
    if not key:
        sys.exit("Missing REDASH_API_KEY (or ~/.uc-claude/redash-api-key).")
    return key


def run_query(sql):
    """Ad-hoc Redash query: submit, poll the job, fetch the full result (no row cap)."""
    s = requests.Session()
    s.headers["Authorization"] = f"Key {redash_key()}"
    r = s.post(f"{REDASH_URL}/api/query_results",
               json={"data_source_id": DS_SNOWFLAKE, "query": sql, "max_age": 0}, timeout=60)
    r.raise_for_status()
    body = r.json()
    if "job" in body:
        job_id = body["job"]["id"]
        for _ in range(360):  # 12 min ceiling
            job = s.get(f"{REDASH_URL}/api/jobs/{job_id}", timeout=30).json()["job"]
            if job["status"] == 3:
                qr_id = job["query_result_id"]
                break
            if job["status"] in (4, 5):
                sys.exit(f"Jarvis query failed: {job.get('error')}")
            time.sleep(2)
        else:
            sys.exit("Jarvis query timed out.")
        body = s.get(f"{REDASH_URL}/api/query_results/{qr_id}", timeout=60).json()
    # Snowflake upper-cases unquoted aliases (ORDER_MONTH); normalise.
    return [{k.lower(): v for k, v in r.items()} for r in body["query_result"]["data"]["rows"]]


def main():
    dry_run = "--dry-run" in sys.argv
    today = datetime.date.today()
    y, m = today.year, today.month - (MONTHS_BACK - 1)
    while m <= 0:
        m += 12
        y -= 1
    start = datetime.date(y, m, 1)
    end = today + datetime.timedelta(days=1)  # through today

    rows = []
    for r in run_query(build_sql(start, end)):
        month = str(r["order_month"])[:7] + "-01"
        rows.append({
            "month_start": month,
            "sku_id": r["sku_id"],
            "sku": SKU_DISPLAY.get(r["sku_id"], r["sku_id"]),
            "net_sales": round(float(r["net_sales"] or 0), 2),
        })

    if dry_run:
        for row in sorted(rows, key=lambda x: (x["month_start"], x["sku"])):
            print(row["month_start"], f'{row["sku"]:8}', round(row["net_sales"]))
        print(f"{len(rows)} row(s), {start} .. {today} (dry run, nothing written)")
        return

    supabase_url, key = supabase_config()
    replace_by_filter(supabase_url, key, "health_sales_monthly", rows,
                      {"month_start": f"gte.{start.isoformat()}"})
    print(f"Synced {len(rows)} health_sales_monthly row(s), {start} .. {today}.")


if __name__ == "__main__":
    main()
