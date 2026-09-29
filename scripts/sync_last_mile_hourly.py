#!/usr/bin/env python3
"""
Last Mile Tracking, hourly leg: asks the carriers what actually happened,
then rebuilds every rollup the portal's Last Mile page reads.

This is the job that makes the page show numbers. sync_last_mile_daily.py
only builds the shipment watchlist (intake); THIS script polls the
couriers, decides what is alertable, and writes all six
last_mile_* rollup tables.

Ported from the awb-delivery-tracker project's
awb_tracker/jobs/hourly_track.py. The analysis modules underneath
(alerts, performance, tiering, dq, and the five LSP adapters) are copied
verbatim into scripts/last_mile_lib/ rather than reimplemented -- they
encode findings measured against live carrier responses that are not
guessable from the outside:

  * Blue Dart's "Records Not Found" string exists as a HIDDEN TEMPLATE
    even on success, so absence must be detected structurally
  * Delhivery drops tracking 7 days after an end state, so a shipment
    never polled inside that window is unrecoverable from Delhivery
  * Uniware's timestamps are stored in a DIFFERENT TIMEZONE PER CARRIER;
    uncorrected, the hour-level thresholds here manufacture false STUCK
    alerts on the majority of volume
  * Shadowfax serves Brotli, which without the `brotli` package returns
    binary that looks like an empty page rather than an error

WHAT CHANGED IN THE PORT
------------------------
Only the I/O. The source project had no database -- it ran as a stateless
cloud routine and kept its watchlist and poll-state in Claude's artifact
DB via a read-shard/run/write-shard dance. This portal has Postgres, so:

    artifact DB  ->  last_mile_watchlist / last_mile_poll_state
    JSON rollups ->  the six last_mile_* tables the Vue page reads

PollState already accepted a `preloaded` dict, which is the seam the
Supabase rows drop into unchanged.

SHADOWFAX
---------
Not polled. Kalrav's call: it needs a manual check against public data or
Uniware's own tracking status rather than an automated adapter, so its
shipments keep whatever status Uniware recorded and are never counted as
carrier-verified. The adapter module is present but this script does not
call it -- see POLLED_ADAPTERS below.

HOLISOL
-------
Not tracked at all, excluded at intake -- user decision 2026-09-28: Holisol
is not an LSP UC actually uses. See last_mile_lib/config.json ->
excluded_adapters. Its shipments never reach `open_ships`/`candidates`
below, so it needs no POLLED_ADAPTERS entry.

CREDENTIALS
-----------
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  BLUEDART_LOGIN_ID + BLUEDART_LICENSE_KEY   -- NOTE the American spelling;
      the portal's existing GitHub secret is BLUEDART_LICENCE_KEY, so the
      workflow maps one to the other. Unset = Blue Dart silently falls
      back to HTML scraping instead of its API, with no error.
  DELHIVERY_API_TOKEN
  DTDC_ACCESS_TOKEN
Every adapter runs on a public path when its credential is absent, so a
missing secret degrades quality rather than failing the run.

Usage:
    python scripts/sync_last_mile_hourly.py [--dry-run] [--limit N]
                                            [--budget-minutes N]
"""
import os
import sys
import time
from collections import defaultdict
from datetime import timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from last_mile_lib import alerts as alerts_mod          # noqa: E402
from last_mile_lib import envfile, performance          # noqa: E402
from last_mile_lib.dates import now_ist                 # noqa: E402
from last_mile_lib.dq import DQSink                     # noqa: E402
from last_mile_lib.lsp import registry                  # noqa: E402
from last_mile_lib.lsp.base import FetchContext, FetchOutcome, TrackingResult  # noqa: E402
from last_mile_lib.lsp.http import HttpClient           # noqa: E402
from last_mile_lib.sla import days_overdue              # noqa: E402
from last_mile_lib.tiering import PollState             # noqa: E402
from last_mile_lib.uniware_status import to_canonical   # noqa: E402
from last_mile_lib.watchlist import Shipment            # noqa: E402

REQUEST_TIMEOUT = 120
PERF_WINDOW_DAYS = 15
WORST_LANES_MIN_VOLUME = 5
WORST_LANES_LIMIT = 25

# Adapters this job actually calls. Shadowfax is deliberately absent (see the
# module docstring); not_trackable/porter/unknown/holisol have nothing to
# call -- holisol is excluded at intake entirely (config.json), never even
# reaching the `candidates` filter below.
POLLED_ADAPTERS = frozenset({"bluedart", "delhivery", "dtdc"})


def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing {name} environment variable.")
    return v


def supabase_headers(key):
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def sb_get(url, key, table, params):
    r = requests.get(f"{url}/rest/v1/{table}", headers=supabase_headers(key),
                     params=params, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        sys.exit(f"Reading {table} failed ({r.status_code}): {r.text[:400]}")
    return r.json()


def sb_write(url, key, table, rows, on_conflict=None):
    """Upsert when on_conflict is given, plain insert otherwise."""
    if not rows:
        return
    headers = {**supabase_headers(key), "Content-Type": "application/json",
               "Prefer": ("resolution=merge-duplicates," if on_conflict else "") + "return=minimal"}
    params = {"on_conflict": on_conflict} if on_conflict else {}
    for i in range(0, len(rows), 500):
        r = requests.post(f"{url}/rest/v1/{table}", headers=headers, params=params,
                          json=rows[i:i + 500], timeout=REQUEST_TIMEOUT)
        if not r.ok:
            sys.exit(f"Writing {table} failed ({r.status_code}): {r.text[:400]}")


def load_watchlist(url, key):
    """Rehydrate Shipment objects from last_mile_watchlist.

    Column names match the dataclass field names except created_at_uniware,
    which is spelled out in the table to make clear it is Uniware's
    timestamp rather than the row's own insert time.
    """
    rows = []
    page = 0
    while True:
        batch = sb_get(url, key, "last_mile_watchlist",
                       {"select": "*", "limit": 1000, "offset": page * 1000})
        rows.extend(batch)
        if len(batch) < 1000:
            break
        page += 1

    fields = {f for f in Shipment.__dataclass_fields__}
    ships = []
    for r in rows:
        r = dict(r)
        r["created_at"] = r.pop("created_at_uniware", None)
        r.pop("last_pulled_at", None)
        ships.append(Shipment(**{k: v for k, v in r.items() if k in fields}))
    return ships


def load_poll_state(url, key):
    """-> {awb: record dict}, the shape PollState(preloaded=...) expects."""
    rows = []
    page = 0
    while True:
        batch = sb_get(url, key, "last_mile_poll_state",
                       {"select": "*", "limit": 1000, "offset": page * 1000})
        rows.extend(batch)
        if len(batch) < 1000:
            break
        page += 1
    out = {}
    for r in rows:
        out[r["awb"]] = {
            "awb": r["awb"],
            "next_poll_at": r.get("next_poll_at"),
            "terminal": bool(r.get("terminal")),
            "confirmed": bool(r.get("confirmed")),
            "last_status": r.get("last_status"),
            "last_polled_at": r.get("last_polled_at"),
            "poll_count": r.get("poll_count") or 0,
            "consecutive_failures": r.get("consecutive_failures") or 0,
        }
    return out


def iso(dt):
    return dt.isoformat() if dt is not None else None


def main():
    dry_run = "--dry-run" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    budget_minutes = 20
    if "--budget-minutes" in sys.argv:
        budget_minutes = int(sys.argv[sys.argv.index("--budget-minutes") + 1])
    # Restrict THIS run's carrier calls to a subset, e.g. --adapters dtdc for
    # a cheap isolated test after rotating one carrier's credential. The run
    # still recomputes and writes all six rollup tables as usual -- an
    # untouched adapter's shipments just keep whatever status they already
    # had (Uniware's own, or a prior poll), same as any hourly run where a
    # given AWB simply wasn't due yet.
    only_adapters = None
    if "--adapters" in sys.argv:
        only_adapters = {x.strip().lower() for x in
                         sys.argv[sys.argv.index("--adapters") + 1].split(",") if x.strip()}
        unknown = only_adapters - POLLED_ADAPTERS
        if unknown:
            sys.exit(f"--adapters: not pollable: {sorted(unknown)}; choose from {sorted(POLLED_ADAPTERS)}")

    url, key = env("SUPABASE_URL").rstrip("/"), env("SUPABASE_SERVICE_ROLE_KEY")
    now = now_ist()
    run_id = f"hourly-{now.strftime('%Y%m%dT%H%M')}"

    ships = load_watchlist(url, key)
    if not ships:
        sys.exit("last_mile_watchlist is empty -- run sync_last_mile_daily.py first.")
    preloaded = load_poll_state(url, key)
    state = PollState(preloaded=preloaded)

    dq = DQSink(state_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dq"))
    http = HttpClient(global_concurrency=12)

    # A real environment variable always wins; .env is a local convenience.
    envfile.load_local_env()
    secrets = envfile.adapter_secrets()
    print(f"watchlist {len(ships):,} | poll-state {len(state.records):,} | "
          f"adapter credentials: {sorted(secrets) or 'none (all public paths)'}")

    ctx = FetchContext(now=now, http=http, secrets=secrets, dq=dq,
                       deadline=now + timedelta(minutes=budget_minutes),
                       dry_run=dry_run)

    # The poll STATE decides what is settled, not the export -- a shipment the
    # carrier already confirmed delivered must never be re-polled just because
    # the rolling window re-exported it.
    settled = state.settled_awbs()
    candidates = [s for s in ships
                  if s.needs_lsp_poll and s.awb not in settled
                  and s.adapter_id in POLLED_ADAPTERS]
    if only_adapters:
        before = len(candidates)
        candidates = [s for s in candidates if s.adapter_id in only_adapters]
        print(f"--adapters {sorted(only_adapters)}: {len(candidates)} of {before} candidates in scope")

    statuses = {s.awb: to_canonical(s.uniware_tracking_status)[0] for s in candidates}
    due, tally = state.due(candidates, statuses, now=now, limit=limit)
    print(f"candidates {len(candidates):,} | due {len(due):,} | "
          f"tiers { {k: v for k, v in tally.items() if v} }")

    grouped = defaultdict(list)
    for s in due:
        grouped[s.adapter_id].append(s.awb)

    results = []
    if dry_run:
        print("dry run -- selection only, no carrier calls")
        for adapter, awbs in sorted(grouped.items()):
            spec = registry.get_spec(adapter)
            flag = "" if (spec and spec.enabled) else "  [DISABLED]"
            print(f"  would poll {len(awbs):>5} via {adapter}{flag}")
    else:
        # Circuit breaker -- ported PRINCIPLE, not the original mechanism.
        # awb_tracker/preflight.py guarded against a specific trigger (a
        # laptop waking from sleep with the VPN not yet back up) that
        # doesn't apply to an always-on GitHub-hosted runner. But the
        # DAMAGE it was built to prevent is architecture-independent: a
        # batch of near-total failures isn't 485 individually-stale
        # shipments, it's the carrier's API being down -- and recording
        # each one as a consecutive_failure poisons tiering (backoff up to
        # 24h) for every one of them, for a problem that had nothing to do
        # with any single AWB. Measured on the source project: one bad
        # night left 1,125 poll records sitting at 2 consecutive failures.
        # So: skip state.record() for a batch that looks like an outage,
        # not an AWB list. Left untouched, those AWBs are simply due again
        # next run -- no penalty, no wasted signal.
        UNHEALTHY_MIN_BATCH = 10   # below this, one bad AWB can't trip it
        UNHEALTHY_MAX_OK_RATE = 0.20
        skipped_adapters = []

        for adapter, awbs in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            mod = registry.get_adapter(adapter)
            if mod is None:
                continue
            t0 = time.time()
            got = mod.track(awbs, ctx)
            results.extend(got)
            oks = sum(1 for r in got if r.outcome == FetchOutcome.OK)
            ok_rate = oks / len(got) if got else 1.0
            unhealthy = len(got) >= UNHEALTHY_MIN_BATCH and ok_rate <= UNHEALTHY_MAX_OK_RATE
            flag = "  [CIRCUIT BREAKER -- state.record() skipped this batch]" if unhealthy else ""
            print(f"  {adapter:<12} polled {len(got):>5} ok={oks:<5} in {time.time() - t0:5.1f}s{flag}")
            if unhealthy:
                skipped_adapters.append(adapter)
            else:
                for r in got:
                    state.record(r, now=now_ist())

        if skipped_adapters:
            print(f"WARN: {sorted(skipped_adapters)} looked like an outage this run "
                  f"(<= {UNHEALTHY_MAX_OK_RATE:.0%} ok on >= {UNHEALTHY_MIN_BATCH} AWBs) -- "
                  f"poll_state left untouched for those shipments, they are simply due again next run.")

    polls = {r.awb: r for r in results if r.outcome == FetchOutcome.OK}

    # ---- rebuild every rollup -------------------------------------------
    al = alerts_mod.evaluate_all(ships, polls=polls, now=now_ist())
    # grade_assumed=True here only -- user decision 2026-09-29: the Carrier
    # Performance view (Open Shipments tab) grades an ASSUMED promise against
    # its 6-day default rather than excluding it. worst_lanes() below is
    # untouched (lsp_city grain, still excludes ASSUMED) -- see build()'s
    # docstring for why that distinction matters there.
    cards = performance.build(ships, grain="lsp", grade_assumed=True)
    lanes = performance.worst_lanes(ships, min_volume=WORST_LANES_MIN_VOLUME,
                                    limit=WORST_LANES_LIMIT)
    # Total lanes ACTUALLY IN USE across every LSP -- user decision 2026-09-29:
    # worst_lanes() itself only returns the curated top WORST_LANES_LIMIT (25)
    # lanes that also clear WORST_LANES_MIN_VOLUME (>=5 graded), so
    # len(lanes) was never "how many lanes exist", only "how many made this
    # curated list". Every (lsp, city) combination with at least one shipment
    # gets its own Scorecard bucket in build() regardless of volume or
    # grading, so this is the real count.
    total_lanes_active = len(performance.build(ships, grain="lsp_city"))
    funnel = performance.coverage_funnel(ships)
    dq_summary = dq.summary()

    by_bucket = defaultdict(int)
    for a in al:
        by_bucket[a.bucket] += 1
    open_ships = [s for s in ships if s.cohort in ("live", "backlog", "no_dispatch_date")]

    # The full "not complete, not RTO" entry point -- every open shipment,
    # not just the curated subset evaluate_all() flags. Uses the SAME fused
    # (carrier-poll-aware) status alerts.fuse() already computed for each of
    # these, so a healthy shipment shows status of the same quality as an
    # alerted one, not a downgraded Uniware-only view.
    alerts_by_awb = {a.awb: a for a in al}
    open_rows = []
    for s in open_ships:
        poll = polls.get(s.awb)
        fused = alerts_mod.fuse(s, poll, now)
        alert = alerts_by_awb.get(s.awb)
        spec = registry.get_spec(s.adapter_id)
        open_rows.append({
            "awb": s.awb, "cohort": s.cohort, "lsp": spec.display_name if spec else s.adapter_id,
            "courier_code": s.courier_code, "facility_code": s.facility_code,
            "city": s.city, "pincode": s.pincode, "channel": s.channel,
            "payment_type": s.payment_type, "sale_order_codes": s.sale_order_codes[:5],
            "item_count": s.item_count, "status": fused.canonical.value, "raw_status": fused.raw,
            "status_source": fused.source, "status_at": fused.at.isoformat() if fused.at else None,
            # Same shipment_tracking parity as last_mile_alerts below -- see
            # that block's comment for why these four are worth carrying
            # through for a shipment that hasn't (yet) tripped an alert too.
            "last_scan_location": (poll.current_location if poll else None),
            "destination": (poll.destination if poll else None),
            "expected_delivery_date": (poll.expected_delivery.date().isoformat()
                                        if poll and poll.expected_delivery else None),
            "last_scan_text": (poll.events[0].raw_status if poll and poll.events else None),
            "raw": (poll.raw_payload if poll else None),
            "promised_date": s.promised_date, "promise_source": s.promise_source,
            "days_overdue": days_overdue(s.promised_date, now),
            "days_since_dispatch": s.days_since_dispatch,
            "has_alert": alert is not None, "primary_flag": alert.primary_flag if alert else None,
            "bucket": alert.bucket if alert else None,
        })

    # delivered/graded surfaced here because a silent 0 across every LSP (as
    # opposed to 0 scorecard/lane ROWS, which this line already showed) is
    # exactly the failure mode that went undetected for weeks -- see dates.py
    # FORMATS, 2026-09-28.
    graded_total = sum(c.on_time + c.late for c in cards)
    print(f"alerts {len(al):,} { dict(by_bucket) } | scorecards {len(cards)} "
          f"(delivered={sum(c.delivered for c in cards):,}, graded={graded_total:,}) "
          f"| lanes {len(lanes)} | open {len(open_rows):,}")

    if dry_run:
        print("[dry-run] nothing written.")
        return

    # ---- write ------------------------------------------------------------
    # last_mile_run goes FIRST: every other table references it, so a run that
    # dies halfway leaves orphan-free partial data rather than rows pointing at
    # a run_id that was never created.
    sb_write(url, key, "last_mile_run", [{
        "run_id": run_id,
        "generated_at": iso(now),
        "window_days": PERF_WINDOW_DAYS,
        "health": "ok",
        "last_hourly_run_at": iso(now),
        "alerts_total": len(al),
        "queue_rescue": by_bucket.get("rescue", 0),
        "queue_closed_failure": by_bucket.get("closed_failure", 0),
        "queue_data_quality": by_bucket.get("data_quality", 0),
        "open_shipments": len(open_ships),
        "live_tracked": len(polls),
        "total_lanes_active": total_lanes_active,
        "scope_channels": sorted({s.channel for s in ships if s.channel}),
        "scope_note": "Spares, refresh kits and RO purifiers. B2B and internal BOM excluded.",
    }], on_conflict="run_id")

    sb_write(url, key, "last_mile_coverage", [{
        "run_id": run_id,
        "shipments_total": funnel.get("shipments_total", len(ships)),
        "open_total": funnel.get("open_total", len(open_ships)),
        "carrier_assigned": funnel.get("carrier_assigned", 0),
        "excluded_by_scope": funnel.get("excluded_by_scope", 0),
        "excluded_by_adapter": funnel.get("excluded_by_adapter") or {},
        "excluded_reasons": funnel.get("excluded_reasons") or {},
        "not_trackable_by_design": funnel.get("not_trackable_by_design", 0),
        "no_adapter_rule": funnel.get("no_adapter_rule", 0),
    }], on_conflict="run_id")

    def dq_part(kind, field):
        return (dq_summary.get(kind) or {}).get(field)

    sb_write(url, key, "last_mile_dq_summary", [{
        "run_id": run_id,
        "unmapped_status_distinct": dq_part("unmapped_status", "distinct") or 0,
        "unmapped_status_occurrences": dq_part("unmapped_status", "total_occurrences") or 0,
        "unmapped_status_top": dq_part("unmapped_status", "top") or [],
        "unmapped_courier_distinct": dq_part("unmapped_courier", "distinct") or 0,
        "unmapped_courier_occurrences": dq_part("unmapped_courier", "total_occurrences") or 0,
        "unmapped_courier_top": dq_part("unmapped_courier", "top") or [],
        "awb_pattern_distinct": dq_part("awb_pattern", "distinct") or 0,
        "awb_pattern_occurrences": dq_part("awb_pattern", "total_occurrences") or 0,
        "awb_pattern_top": dq_part("awb_pattern", "top") or [],
        "missing_awb_distinct": dq_part("missing_awb", "distinct") or 0,
        "missing_awb_occurrences": dq_part("missing_awb", "total_occurrences") or 0,
        "missing_awb_top": dq_part("missing_awb", "top") or [],
    }], on_conflict="run_id")

    sb_write(url, key, "last_mile_lsp_perf", [{
        "run_id": run_id, "lsp": c.lsp, "courier_codes": c.courier_codes,
        "delivered": c.delivered, "on_time": c.on_time, "late": c.late,
        "on_time_pct": c.on_time_pct, "excluded": c.excluded_assumed_promise,
    } for c in cards], on_conflict="run_id,lsp")

    # Keys are exactly what performance.worst_lanes() returns at lsp_city
    # grain -- no pincode, no facility, no "delivered". Verified against that
    # function rather than assumed.
    sb_write(url, key, "last_mile_worst_lanes", [{
        "run_id": run_id, "lsp": l.get("lsp") or "", "city": l.get("city"),
        "graded": l.get("graded") or 0, "late": l.get("late") or 0,
        "on_time_pct": l.get("on_time_pct"),
        "avg_transit_days": l.get("avg_transit_days"),
        "p85_transit_days": l.get("p85_transit_days"),
        "promised_tat_days": l.get("promised_tat_days"),
        "active": l.get("active") or 0, "breached": l.get("breached") or 0,
        "rto_in_flight": l.get("rto_in_flight") or 0,
        "excluded_assumed_promise": l.get("excluded_assumed_promise") or 0,
    } for l in lanes])

    sb_write(url, key, "last_mile_alerts", [{
        "run_id": run_id, "awb": a.awb, "flags": a.flags, "primary_flag": a.primary_flag,
        "bucket": a.bucket, "severity": a.severity, "lsp": a.lsp,
        "courier_code": a.courier_code, "facility_code": a.facility_code,
        "city": a.city, "pincode": a.pincode, "channel": a.channel,
        "category": a.category,
        "payment_type": a.payment_type, "sale_order_codes": a.sale_order_codes,
        "item_count": a.item_count, "status": a.status, "raw_status": a.raw_status,
        "status_source": a.status_source, "status_at": a.status_at,
        "last_scan_location": a.last_scan_location, "promised_date": a.promised_date,
        "promise_source": a.promise_source, "days_overdue": a.days_overdue,
        "days_since_dispatch": a.days_since_dispatch, "hours_since_scan": a.hours_since_scan,
        "attempts": a.attempts, "ndr_reason": a.ndr_reason,
        # shipment_tracking parity (the AWB tracker's inbound counterpart):
        # destination/expected_delivery_date/last_scan_text/raw, straight off
        # the same LSP poll that already fills last_scan_location above.
        "destination": a.destination, "expected_delivery_date": a.expected_delivery_date,
        "last_scan_text": a.last_scan_text, "raw": a.raw,
        "notes": "; ".join(a.notes) if a.notes else None,
    } for a in al], on_conflict="run_id,awb")

    sb_write(url, key, "last_mile_open_shipments",
             [{**r, "run_id": run_id} for r in open_rows], on_conflict="run_id,awb")

    # Poll state last: it is the only table whose loss is merely a wasted
    # re-poll next run, so it is the safest thing to leave until the end.
    sb_write(url, key, "last_mile_poll_state", [{
        "awb": rec.awb,
        "next_poll_at": rec.next_poll_at,
        "terminal": rec.terminal,
        "confirmed": rec.confirmed,
        "last_status": rec.last_status,
        "last_polled_at": rec.last_polled_at,
        "poll_count": rec.poll_count,
        "consecutive_failures": rec.consecutive_failures,
        "updated_at": iso(now),
    } for rec in state.records.values()], on_conflict="awb")

    print(f"Wrote run {run_id}: {len(al)} alert(s), {len(cards)} scorecard(s), "
          f"{len(lanes)} lane(s), {len(state.records)} poll-state record(s).")


if __name__ == "__main__":
    main()
