#!/usr/bin/env python3
"""
S&OP: Daily Dispatch Planner tab.

Runs the first-mile dispatch engine -- the same engine the
/first-mile-dispatch-decision skill runs, vendored at scripts/vendor/
first_mile_dispatch.js -- and publishes its plan to Supabase.

WHY A JS ENGINE IN A PYTHON REPO: the allocator is ~2,000 lines of tier
scoring, DOI laddering and scarce/surplus truck packing whose exact
behaviour the business already trusts. Re-implementing it in Python would
guarantee the portal and the skill eventually disagree, and disagreeing
about which truck goes where is worse than the language mismatch. So this
script does the I/O -- fetch sheets, run the engine, write Supabase -- and
the engine does the thinking.

TWO RUNS PER CYCLE. The skill asks the operator, every run, whether today's
production should count as dispatchable: it is only safe to add on top of
the FG snapshot if that snapshot predates today's finished output (morning),
and double-counts it otherwise (evening). There is no timestamp on the FG
table to decide automatically. The portal can't ask, so it computes BOTH
and lets the tab toggle between them -- which is strictly more useful than
the prompt, since you can see what today's run actually buys you.

The engine is READ-ONLY here. The skill overwrites the live 'First Mile
Plan' sheet tab on every run; the vendored copy has that code removed
outright, because a cron that clears and rewrites a live sheet will sooner
or later wipe someone's in-progress edit.

Credentials: GOOGLE_SERVICE_ACCOUNT_JSON, SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY (all already provisioned, no new secrets).
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import zoneinfo

import requests

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from sop_common import (  # noqa: E402
    COPY_DAILY_INPUT_ANISH_ID, REQUEST_TIMEOUT, SKUS, WH_CHANNEL_SKU_ID, get_access_token,
    replace_by_filter, supabase_config, to_num,
)

ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "first_mile_dispatch.js")

# Every tab the engine's parsers touch, per workbook. The keys are the names the engine looks up in
# its mkWb() shim; the tab names must match the sheet exactly -- note the trailing space on the
# Diwali tab, which is real and which the engine's prefix match tolerates but values.get does not.
SOURCE_TABS = {
    "copy_daily_input_anish": (COPY_DAILY_INPUT_ANISH_ID, ["Dispatch Planning", "Raw Data Sheet"]),
    "wh_channel_sku": (WH_CHANNEL_SKU_ID, ["Current Inventory", "Day wise trackr",
                                            "Diwali Sales Plan - Overall "]),
}

SCENARIOS = {
    # scenario key -> extra engine flags
    "EXCLUDE_TODAY": ["--no-today-production"],
    "INCLUDE_TODAY": [],
}


def fetch_rows(token, spreadsheet_id, tab):
    """One tab as a raw row matrix.

    valueRenderOption=UNFORMATTED_VALUE is REQUIRED, not a preference: the engine reads
    'Raw Data Sheet'!B and 'Day wise trackr'!A as Excel date SERIALS via toNum(). Formatted display
    strings would each evaluate to 0 and every dated row would be silently skipped -- which is
    exactly the silent-zero failure mode this pipeline has already been bitten by twice."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{requests.utils.quote(repr_tab(tab))}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                         params={"valueRenderOption": "UNFORMATTED_VALUE",
                                 "dateTimeRenderOption": "SERIAL_NUMBER"},
                         timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("values", [])


def repr_tab(tab):
    return f"'{tab}'"


def build_payload(token):
    payload = {}
    for book, (spreadsheet_id, tabs) in SOURCE_TABS.items():
        payload[book] = {}
        for tab in tabs:
            rows = fetch_rows(token, spreadsheet_id, tab)
            if not rows:
                sys.exit(f"'{tab}' came back empty -- aborting rather than planning on a blank tab.")
            payload[book][tab] = rows
            print(f"  {book}/{tab}: {len(rows)} rows", flush=True)
    return payload


def run_engine(rows_path, scenario, out_dir):
    """One engine run. --no-suggest is deliberate: the production-suggestion engine re-runs the whole
    simulation in a child process once per scarce SKU, which is fine interactively but multiplies a
    cron's runtime for output this tab doesn't show."""
    json_path = os.path.join(out_dir, f"{scenario}.json")
    cmd = ["node", ENGINE, "--rows", rows_path, "--json", json_path,
           "--no-suggest"] + SCENARIOS[scenario]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        sys.exit(f"Engine failed for {scenario} (exit {proc.returncode}):\n{tail}")
    with open(json_path) as f:
        return json.load(f)


def guard(result, scenario):
    """The engine degrades quietly on bad input -- both of its silent-zero modes have burned this
    pipeline before (a column shift that emptied PO demand for 11 days, and a renamed label that
    zeroed plant FG). Refuse to publish a plan built on either."""
    if not result["plant"]["found"]:
        sys.exit(f"{scenario}: plant FG did not parse -- refusing to publish a plan that ignores "
                 f"all finished goods at Ronch and Amber.")
    if result["poOrderedTotal"] <= 0:
        sys.exit(f"{scenario}: zero PO demand parsed across the whole window. That is not a real "
                 f"state -- check the Raw Data Sheet column indices before trusting this run.")
    if not result["dispatchPlan"]:
        print(f"WARNING: {scenario} produced no dispatch events at all.", file=sys.stderr)


def tab_rows(result, name):
    """A report tab as a list of dicts keyed by its own header row."""
    rows = result["tabs"].get(name) or []
    if len(rows) < 2:
        return []
    header = [str(h or "").strip() for h in rows[0]]
    return [dict(zip(header, r)) for r in rows[1:]]


def build_rows(result, scenario, run_date):
    """Engine output -> the four Supabase tables."""
    base = {"run_date": run_date, "scenario": scenario}

    plan = [
        {**base,
         "dispatch_date": ev["date"], "facility": ev["facility"], "warehouse": ev["wh"],
         "sku": sku, "qty": to_num(qty), "truck_total": to_num(ev["total"]),
         "eta": ev["eta"], "reason": ev["reason"], "tier": ev.get("tier")}
        for ev in result["dispatchPlan"] for sku, qty in ev["skus"].items()
    ]

    fg, hold = result["plant"]["fg"], result["plant"]["hold"]
    today_prod = result["plant"]["todayProduction"] or {}
    yield_factor = result["productionYield"]
    plant = []
    for facility in ("RONCH", "AMBER"):
        for sku in SKUS:
            raw = to_num((today_prod.get(facility) or {}).get(sku, 0))
            plant.append({**base, "facility": facility, "sku": sku,
                          "fg_qty": to_num((fg.get(facility) or {}).get(sku, 0)),
                          "hold_qty": to_num((hold.get(facility) or {}).get(sku, 0)),
                          "production_raw": raw,
                          "production_yielded": round(raw * yield_factor)})

    fill = [
        {**base, "sku": r["SKU"], "ordered": to_num(r["PO Ordered"]), "served": to_num(r["PO Served"]),
         "short": to_num(r["Short"]), "fill_pct": to_num(r["Fill %"]),
         "hard_deficit": to_num(r.get("Hard Supply Deficit", 0)),
         "dispatch_fixable": to_num(r.get("Dispatch-Fixable", 0))}
        for r in tab_rows(result, "PO Fill Rate")
        if str(r.get("SKU", "")).strip() in SKUS
    ]

    util = [
        {**base, "facility": r["Facility"], "opening_fg": to_num(r["Opening FG"]),
         "production": to_num(r.get(f"Production (x{yield_factor})", 0)),
         "dispatched": to_num(r["Dispatched Units"]), "trucks": int(to_num(r["Trucks"])),
         "residual": to_num(r["Residual Units"])}
        for r in tab_rows(result, "Facility Utilization")
        if str(r.get("Facility", "")).strip() in ("RONCH", "AMBER")
    ]

    # The POs this plan leaves short. `short` here sums exactly to the headline short in the fill
    # rate rows above -- if that ever stops being true, the sheet-order apportionment in the engine
    # has drifted and the detail is no longer trustworthy.
    missed = [
        {**base,
         "po_date": r["day"], "po_number": r["poNumber"] or None, "so_number": r["soNumber"] or None,
         "warehouse": r["wh"], "channel": r["channel"], "sku": r["sku"],
         "ordered": to_num(r["ordered"]), "served": to_num(r["served"]),
         "short": to_num(r["short"]), "status": r["status"]}
        for r in result.get("missedPORows", [])
    ]

    return plan, plant, fill, util, missed


def main():
    supabase_url, supabase_key = supabase_config()
    token = get_access_token()

    print("Fetching source tabs...", flush=True)
    payload = build_payload(token)

    run_date = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Kolkata")).date().isoformat()
    all_plan, all_plant, all_fill, all_util, all_missed = [], [], [], [], []

    with tempfile.TemporaryDirectory() as tmp:
        rows_path = os.path.join(tmp, "rows.json")
        with open(rows_path, "w") as f:
            json.dump(payload, f)

        for scenario in SCENARIOS:
            print(f"\nRunning engine: {scenario} ...", flush=True)
            result = run_engine(rows_path, scenario, tmp)
            guard(result, scenario)
            plan, plant, fill, util, missed = build_rows(result, scenario, run_date)
            print(f"  {len(plan)} plan row(s), {len(result['dispatchPlan'])} truck(s), "
                  f"PO ordered {result['poOrderedTotal']:.0f}, {len(missed)} PO(s) at risk")
            # The detail must tie to the headline, or the tab would show per-PO numbers that don't
            # add up to the fill rate sitting right above them.
            #
            # Tolerance, not equality, and deliberately so: the fill-rate row computes
            # short = ordered - round(served) once per SKU, while the per-PO shorts stay fractional
            # (ambient DRR depletes balances by fractions of a unit). Measured 2026-09-17, every SKU
            # lands within 0.34 units of its headline, so a whole-run gap of a few units is rounding.
            # Anything larger means the sheet-order apportionment has genuinely drifted.
            detail_short = sum(r["short"] for r in missed)
            headline_short = sum(r["short"] for r in fill)
            tolerance = len(SKUS)  # at most ~1 unit of rounding per SKU
            if abs(detail_short - headline_short) > tolerance:
                sys.exit(f"{scenario}: per-PO shortfalls sum to {detail_short:.1f} but the fill-rate "
                         f"total is {headline_short:.1f} (tolerance {tolerance}) -- the apportionment "
                         f"no longer reconciles; refusing to publish inconsistent numbers.")
            all_plan += plan
            all_plant += plant
            all_fill += fill
            all_util += util
            all_missed += missed

    where = {"run_date": f"eq.{run_date}"}
    replace_by_filter(supabase_url, supabase_key, "sop_first_mile_plan", all_plan, where)
    replace_by_filter(supabase_url, supabase_key, "sop_first_mile_plant", all_plant, where)
    replace_by_filter(supabase_url, supabase_key, "sop_first_mile_fill_rate", all_fill, where)
    replace_by_filter(supabase_url, supabase_key, "sop_first_mile_facility_util", all_util, where)
    # allow_empty: a plan that serves every committed order is the good outcome, not a parse failure.
    replace_by_filter(supabase_url, supabase_key, "sop_first_mile_missed_po", all_missed, where,
                       allow_empty=True)

    print(f"\nSynced {len(all_plan)} plan, {len(all_plant)} plant, {len(all_fill)} fill-rate, "
          f"{len(all_util)} utilization and {len(all_missed)} at-risk-PO row(s) across "
          f"{len(SCENARIOS)} scenarios for {run_date}.")


if __name__ == "__main__":
    main()
