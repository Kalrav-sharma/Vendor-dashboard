"""LSP performance scorecards over a rolling 15-day window.

Deliberately computed from the SAME daily pull that feeds the watchlist: the
45-day `addedOn` export already contains the delivered history a 15-day
scorecard needs, so no separate archive has to be maintained and kept in sync
just to answer "how is this carrier doing".

Two honesty rules are baked in, because this is the table that goes into an
LSP conversation:

  1. A lane whose promise was ASSUMED (no rule in SERVICEABILITYRULES_DP) is
     counted in a SEPARATE denominator. Its on-time number is not evidence, and
     presenting it as if it were would be indefensible in front of a carrier.
  2. Nothing is aggregated across carriers at unequal coverage. Every row
     carries its own denominator and its own coverage, so a number can always
     be traced to what it was computed from.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Iterable

from .dates import now_ist, parse_dt
from .lsp import registry
from .watchlist import EXCLUDED_ADAPTERS
from .lsp.base import CanonicalStatus as C
from .sla import days_overdue
from .uniware_status import to_canonical
from .uniware_tz import to_ist
from .watchlist import Shipment

PERF_WINDOW_DAYS = 15


def percentile(sorted_vals: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. Ported from sla-remap pipeline_tat.percentile."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


@dataclass
class Scorecard:
    grain: str                    # lsp | lsp_city | lsp_facility
    lsp: str
    dimension: str = ""           # city or facility value, blank at lsp grain
    courier_codes: list[str] = field(default_factory=list)

    delivered: int = 0
    on_time: int = 0
    late: int = 0
    on_time_pct: float | None = None
    #: Deliveries excluded from on_time_pct because the promise was assumed.
    excluded_assumed_promise: int = 0

    avg_transit_days: float | None = None
    p85_transit_days: float | None = None
    worst_transit_days: float | None = None

    active: int = 0
    breached: int = 0
    at_risk: int = 0
    stuck: int = 0
    ndr: int = 0
    rto_in_flight: int = 0
    rto_completed: int = 0
    lost: int = 0
    not_found: int = 0

    rto_rate_pct: float | None = None
    breach_rate_pct: float | None = None

    #: Coverage -- the denominator behind every number above.
    shipments_seen: int = 0
    with_carrier_status: int = 0
    coverage_pct: float | None = None
    adapter_enabled: bool = True
    adapter_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _transit_days(ship: Shipment) -> float | None:
    """Dispatch -> delivery, in days. This is the LSP's own clock."""
    disp = to_ist(ship.dispatch_date, ship.adapter_id)
    deliv = to_ist(ship.delivery_time, ship.adapter_id)
    if disp is None or deliv is None:
        return None
    days = (deliv - disp).total_seconds() / 86400.0
    return round(days, 2) if days >= 0 else None


def _finalise(sc: Scorecard) -> Scorecard:
    graded = sc.on_time + sc.late
    if graded:
        sc.on_time_pct = round(100.0 * sc.on_time / graded, 1)
    if sc.delivered:
        sc.rto_rate_pct = round(100.0 * (sc.rto_in_flight + sc.rto_completed)
                                / max(1, sc.delivered + sc.rto_in_flight
                                      + sc.rto_completed), 1)
    if sc.active:
        sc.breach_rate_pct = round(100.0 * sc.breached / sc.active, 1)
    if sc.shipments_seen:
        sc.coverage_pct = round(100.0 * sc.with_carrier_status / sc.shipments_seen, 1)
    spec = registry.get_spec(sc.lsp_key) if hasattr(sc, "lsp_key") else None
    return sc


def build(shipments: Iterable[Shipment], now: datetime | None = None,
          window_days: int = PERF_WINDOW_DAYS,
          grain: str = "lsp") -> list[Scorecard]:
    """Aggregate shipments into scorecards at the requested grain."""
    now = now or now_ist()
    cutoff = now - timedelta(days=window_days)

    buckets: dict[tuple[str, str], Scorecard] = {}
    transit: dict[tuple[str, str], list[float]] = defaultdict(list)
    couriers: dict[tuple[str, str], set[str]] = defaultdict(set)

    def key_for(s: Shipment) -> tuple[str, str]:
        if grain == "lsp_city":
            return (s.adapter_id, s.city or "(unknown)")
        if grain == "lsp_facility":
            return (s.adapter_id, s.facility_code or "(unknown)")
        return (s.adapter_id, "")

    for s in shipments:
        # Excluded-by-scope carriers never get a scorecard. Without this they
        # would still appear here, because build() is handed EVERY shipment
        # (it needs delivered ones for on-time %, and those are not in the
        # open set). config.json -> excluded_adapters is the single source.
        if s.cohort == "excluded" or s.adapter_id in EXCLUDED_ADAPTERS:
            continue

        key = key_for(s)
        spec = registry.get_spec(s.adapter_id)
        sc = buckets.get(key)
        if sc is None:
            sc = Scorecard(
                grain=grain,
                lsp=(spec.display_name if spec else s.adapter_id),
                dimension=key[1],
                adapter_enabled=bool(spec.enabled) if spec else False,
                adapter_note=(spec.disabled_reason if spec else
                              f"no adapter for courier {s.courier_code!r}"))
            sc.lsp_key = s.adapter_id  # type: ignore[attr-defined]
            buckets[key] = sc
        couriers[key].add(s.courier_code)

        sc.shipments_seen += 1
        if s.uniware_tracking_status:
            sc.with_carrier_status += 1

        canon, _ = to_canonical(s.uniware_tracking_status)
        delivered_at = to_ist(s.delivery_time, s.adapter_id)
        in_window = delivered_at is not None and delivered_at >= cutoff

        if s.item_status == "DELIVERED" or canon == C.DELIVERED:
            if not in_window:
                continue                      # outside the rolling window
            sc.delivered += 1
            td = _transit_days(s)
            if td is not None:
                transit[key].append(td)
            # Grade against the promise -- but only where the promise is real.
            if s.promise_source == "ASSUMED":
                sc.excluded_assumed_promise += 1
            else:
                overdue = days_overdue(s.promised_date, delivered_at)
                if overdue is None:
                    sc.excluded_assumed_promise += 1
                elif overdue <= 0:
                    sc.on_time += 1
                else:
                    sc.late += 1
            continue

        if canon == C.RTO_DELIVERED:
            sc.rto_completed += 1
            continue
        if canon in (C.RTO_INITIATED, C.RTO_IN_TRANSIT):
            sc.rto_in_flight += 1
            continue
        if canon == C.LOST_OR_DAMAGED:
            sc.lost += 1
            continue
        if canon == C.AWB_NOT_FOUND:
            sc.not_found += 1

        if s.cohort in ("live", "backlog", "no_dispatch_date"):
            sc.active += 1
            overdue = days_overdue(s.promised_date, now)
            if overdue is not None and overdue > 0:
                sc.breached += 1
            elif overdue is not None and -1 <= overdue <= 0:
                sc.at_risk += 1
            if canon == C.DELIVERY_ATTEMPT_FAILED:
                sc.ndr += 1

    out: list[Scorecard] = []
    for key, sc in buckets.items():
        vals = sorted(transit[key])
        if vals:
            sc.avg_transit_days = round(sum(vals) / len(vals), 2)
            sc.p85_transit_days = round(percentile(vals, 0.85) or 0, 2)
            sc.worst_transit_days = vals[-1]
        sc.courier_codes = sorted(c for c in couriers[key] if c)
        out.append(_finalise(sc))

    # Biggest carrier first -- that is where a percentage point matters most.
    out.sort(key=lambda x: (-x.shipments_seen, x.lsp, x.dimension))
    return out


def worst_lanes(shipments: Iterable[Shipment], now: datetime | None = None,
                min_volume: int = 5, limit: int = 15) -> list[dict[str, Any]]:
    """The lanes to raise first: worst on-time% at defensible volume.

    `min_volume` exists so a lane with two late shipments cannot present itself
    as a 0%-on-time crisis.
    """
    cards = [c for c in build(shipments, now, grain="lsp_city")
             if (c.on_time + c.late) >= min_volume]
    cards.sort(key=lambda c: (c.on_time_pct if c.on_time_pct is not None else 101,
                              -(c.on_time + c.late)))
    return [{
        "lsp": c.lsp, "city": c.dimension, "graded": c.on_time + c.late,
        "on_time_pct": c.on_time_pct, "late": c.late,
        "avg_transit_days": c.avg_transit_days, "p85_transit_days": c.p85_transit_days,
        "active": c.active, "breached": c.breached,
        "rto_in_flight": c.rto_in_flight,
        "excluded_assumed_promise": c.excluded_assumed_promise,
    } for c in cards[:limit]]


def coverage_funnel(shipments: list[Shipment]) -> dict[str, Any]:
    """The four-step funnel the dashboard shows instead of a footnote."""
    total = len(shipments)
    open_ships = [s for s in shipments if s.cohort in
                  ("live", "backlog", "no_dispatch_date")]

    # Excluded-by-scope volume never reaches open_ships, so it cannot appear in
    # any step below. It is counted here anyway, once, because a funnel that
    # does not account for its own input lets the board claim coverage of a
    # shrunken denominator -- which is the exact failure the funnel exists to
    # prevent. See config.json -> excluded_adapters.
    excluded = [s for s in shipments if s.cohort == "excluded"]
    excluded_by = Counter(s.adapter_id for s in excluded)
    excluded_reasons = Counter()
    for s in excluded:
        cc = (s.courier_code or "").upper()
        if s.adapter_id == "not_trackable":
            excluded_reasons["in_house_fleet" if cc in ("SELF", "SELF_PICKUP")
                             else "no_courier_assigned"] += 1
        elif s.adapter_id == "unknown":
            excluded_reasons[f"no_adapter_rule:{cc or 'blank'}"] += 1
        else:
            excluded_reasons[s.adapter_id] += 1

    not_trackable = [s for s in open_ships if s.adapter_id == "not_trackable"]
    unknown = [s for s in open_ships if s.adapter_id == "unknown"]
    assigned = [s for s in open_ships
                if s.adapter_id not in ("not_trackable", "unknown")]
    enabled, blocked = [], []
    for s in assigned:
        spec = registry.get_spec(s.adapter_id)
        (enabled if (spec and spec.enabled) else blocked).append(s)

    reasons = Counter()
    for s in not_trackable:
        reasons["in_house_fleet" if s.courier_code.upper() in ("SELF", "SELF_PICKUP")
                else "no_courier_assigned"] += 1

    blocked_by = Counter(s.adapter_id for s in blocked)
    return {
        "shipments_total": total,
        "open_total": len(open_ships),
        "excluded_by_scope": len(excluded),
        "excluded_by_adapter": dict(excluded_by),
        "excluded_reasons": dict(excluded_reasons),
        "not_trackable_by_design": len(not_trackable),
        "not_trackable_reasons": dict(reasons),
        "no_adapter_rule": len(unknown),
        "carrier_assigned": len(assigned),
        "live_tracked": len(enabled),
        "blocked_pending_credentials": len(blocked),
        "blocked_by_adapter": dict(blocked_by),
        "tracked_pct_of_open": (round(100.0 * len(enabled) / len(open_ships), 1)
                                if open_ships else None),
        "tracked_pct_of_assigned": (round(100.0 * len(enabled) / len(assigned), 1)
                                    if assigned else None),
        "with_carrier_status": sum(1 for s in open_ships if s.uniware_tracking_status),
        "without_carrier_status": sum(1 for s in open_ships
                                      if not s.uniware_tracking_status),
        "assumed_promise": sum(1 for s in open_ships if s.promise_source == "ASSUMED"),
    }
