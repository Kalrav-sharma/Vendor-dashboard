"""Alert engine -- turns a shipment plus its carrier status into flagged cases.

Two status sources are fused, deliberately:

  Uniware's `Shipping Tracking Status` -- free, present on ~97% of open in-scope
      AWBs, refreshed roughly 4x/day. This is the baseline.
  A live LSP poll -- fresh to the hour, but only worth spending on shipments
      that are actually at risk.

The disagreement between them is itself a signal, not noise. A shipment Uniware
still calls DISPATCHED while the carrier says delivered means Uniware's status
is stale; the reverse means something is wrong with our data.

Every rule here is deterministic. Nothing in this module needs a judgement
call, which is what keeps the hourly agent cheap -- it reads a summary and only
looks closely at what this module flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Iterable

from .dates import now_ist, parse_dt
from .watchlist import LIVE_POLLED_ADAPTERS
from .lsp.base import CanonicalStatus as C
from .lsp.base import FetchOutcome, TrackingResult
from .lsp import registry
from .sla import days_overdue
from .uniware_status import is_declared_delay, to_canonical
from .uniware_tz import to_ist
from .watchlist import Shipment

# ---------------------------------------------------------------- thresholds
STUCK_IN_TRANSIT_HOURS = 24
STUCK_OUT_FOR_DELIVERY_HOURS = 12
NO_PICKUP_HOURS = 12
AT_RISK_WITHIN_DAYS = 1
AWB_NOT_FOUND_ESCALATE_HOURS = 48

#: Severity is what the dashboard sorts by, so it encodes "who needs to act
#: first", not merely "how unusual is this".
SEVERITY: dict[str, int] = {
    "LOST_SUSPECTED": 100,
    "NDR_FAILED_ATTEMPT": 85,
    "BREACHED": 80,
    "STUCK": 70,
    "NO_PICKUP": 65,
    "AWB_NOT_FOUND": 60,
    "ON_HOLD": 55,
    "CARRIER_DECLARED_DELAY": 50,
    "AT_RISK": 40,
    "STATUS_MISMATCH": 35,
    "CANCELLED_BUT_MOVING": 30,
    "NO_DISPATCH_DATE": 25,
    "LSP_BLOCKED": 20,
    "UNMAPPED_STATUS": 15,
}

#: Which queue a flag belongs to. The team asked to handle LSPs and escalations
#: separately, so the board is split rather than merely sorted:
#:
#:   rescue         -- a FORWARD shipment that can still be delivered on time,
#:                     or nearly. This is where chasing the carrier changes the
#:                     customer's outcome, so it is the default view.
#:   returns        -- an RTO already heading back. Worth chasing to closure for
#:                     restock and refund, but the delivery is lost either way.
#:   closed_failure -- the shipment is gone: lost or damaged in the carrier's
#:                     hands. Nothing to chase for delivery; the CUSTOMER needs
#:                     action (refund or reship).
#:
#: A COMPLETED return (RTO_DELIVERED, "returned to seller and closed") is not an
#: alert at all -- it is settled, and evaluate() drops it. It still counts as
#: `rto_completed` in the carrier scorecards, which is where it belongs.
#:   data_quality   -- our own data is blocking a decision.
#:
#: Returns are separated for a measured reason: they accumulate in the open set
#: because Uniware never closes them, so at one facility 799 of 1,341 open
#: shipments were RTOs. Left in one queue they bury the ~345 forward cases where
#: intervention still matters.
BUCKET: dict[str, str] = {
    "LOST_SUSPECTED": "closed_failure",
    "NDR_FAILED_ATTEMPT": "rescue",
    "BREACHED": "rescue",
    "STUCK": "rescue",
    "NO_PICKUP": "rescue",
    "ON_HOLD": "rescue",
    "CARRIER_DECLARED_DELAY": "rescue",
    "AT_RISK": "rescue",
    "AWB_NOT_FOUND": "data_quality",
    "STATUS_MISMATCH": "data_quality",
    "CANCELLED_BUT_MOVING": "data_quality",
    "NO_DISPATCH_DATE": "data_quality",
    "LSP_BLOCKED": "data_quality",
    "UNMAPPED_STATUS": "data_quality",
}

#: Order the dashboard renders the queues in.
#: "returns" was removed 2026-09-09: every return status is now terminal and
#: leaves the board, so the queue could only ever render empty. Returns stay
#: visible as `rto_rate_pct` in the carrier scorecards.
BUCKET_ORDER: tuple[str, ...] = ("rescue", "closed_failure", "data_quality")

HUMAN: dict[str, str] = {
    "LOST_SUSPECTED": "Carrier reports the shipment lost or damaged",
    "NDR_FAILED_ATTEMPT": "Delivery attempted and failed (NDR)",
    "BREACHED": "Past the promised delivery date, still undelivered",
    "STUCK": "No carrier scan movement for longer than expected",
    "NO_PICKUP": "Dispatched by the warehouse but the carrier never scanned a pickup",
    "AWB_NOT_FOUND": "Carrier does not recognise this AWB",
    "ON_HOLD": "Held by the carrier",
    "CARRIER_DECLARED_DELAY": "Carrier itself has flagged the shipment delayed",
    "AT_RISK": "Due today or tomorrow and not yet out for delivery",
    "STATUS_MISMATCH": "Uniware and the carrier disagree on the status",
    "CANCELLED_BUT_MOVING": "Order is cancelled but the shipment is still in transit",
    "NO_DISPATCH_DATE": "No dispatch date recorded, so no transit clock",
    "LSP_BLOCKED": "No adapter for this carrier -- credentials pending",
    "UNMAPPED_STATUS": "Carrier returned a status we do not recognise yet",
}


@dataclass
class Alert:
    awb: str
    flags: list[str] = field(default_factory=list)
    severity: int = 0
    primary_flag: str = ""
    bucket: str = ""
    # context the team needs to act without opening another system
    lsp: str = ""
    courier_code: str = ""
    facility_code: str = ""
    city: str = ""
    pincode: str = ""
    channel: str = ""
    payment_type: str = ""   # COD | Prepaid | "" -- a stuck COD parcel is a
                             # cash-recovery problem, not just a delivery one
    sale_order_codes: list[str] = field(default_factory=list)
    item_count: int = 0
    status: str = ""
    raw_status: str | None = None
    status_source: str = ""          # lsp | uniware | none
    status_at: str | None = None
    last_scan_location: str | None = None
    promised_date: str | None = None
    promise_source: str = ""
    days_overdue: int | None = None
    days_since_dispatch: int | None = None
    hours_since_scan: float | None = None
    attempts: int | None = None
    ndr_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FusedStatus:
    canonical: C
    raw: str | None
    source: str            # lsp | uniware | none
    at: datetime | None
    mapped: bool = True
    mismatch: bool = False
    uniware_canonical: C | None = None
    lsp_canonical: C | None = None


def fuse(ship: Shipment, poll: TrackingResult | None,
         now: datetime | None = None) -> FusedStatus:
    """Combine Uniware's status with a live poll, preferring the fresher truth.

    A successful LSP poll wins: it is the carrier's own answer, right now.
    Uniware is the fallback and the cross-check.
    """
    now = now or now_ist()
    uni_canon, uni_mapped = to_canonical(ship.uniware_tracking_status)
    uni_at = to_ist(ship.delivery_time or ship.dispatch_date, ship.adapter_id)

    lsp_canon: C | None = None
    if poll is not None and poll.outcome == FetchOutcome.OK:
        lsp_canon = poll.canonical_status

    if lsp_canon is not None:
        mismatch = (
            ship.uniware_tracking_status != ""
            and uni_canon != C.UNKNOWN
            and lsp_canon != uni_canon
        )
        return FusedStatus(
            canonical=lsp_canon, raw=poll.raw_status, source="lsp",
            at=poll.status_at, mapped=poll.raw_status_mapped, mismatch=mismatch,
            uniware_canonical=uni_canon, lsp_canonical=lsp_canon)

    # No usable poll: fall back to Uniware, but keep NOT_FOUND if that is what
    # the carrier actually said.
    if poll is not None and poll.outcome == FetchOutcome.NOT_FOUND:
        return FusedStatus(canonical=C.AWB_NOT_FOUND, raw=poll.raw_status,
                           source="lsp", at=None, uniware_canonical=uni_canon)

    # Uniware is NOT an acceptable answer for a carrier we poll ourselves.
    # Measured 2026-09-14: of 25 Blue Dart AWBs whose status came from Uniware,
    # 22 were already DELIVERED at the carrier and none agreed. Falling back
    # here produced confident, wrong statuses -- "delivery attempt failed" on
    # parcels delivered days earlier -- which is worse than admitting we have
    # not looked yet. config.json -> live_polled_adapters is the source.
    if ship.uniware_tracking_status and ship.adapter_id not in LIVE_POLLED_ADAPTERS:
        return FusedStatus(canonical=uni_canon,
                           raw=ship.uniware_courier_status or ship.uniware_tracking_status,
                           source="uniware", at=uni_at, mapped=uni_mapped,
                           uniware_canonical=uni_canon)

    return FusedStatus(canonical=C.UNKNOWN, raw=None, source="none", at=None,
                       uniware_canonical=uni_canon)


def evaluate(ship: Shipment, poll: TrackingResult | None = None,
             now: datetime | None = None) -> Alert | None:
    """Return an Alert if this shipment needs attention, else None."""
    now = now or now_ist()
    fused = fuse(ship, poll, now)
    flags: list[str] = []
    notes: list[str] = []

    # Settled shipments leave the live report entirely.
    #
    # RTO_DELIVERED ("returned to seller and closed") is settled too, by user
    # decision 2026-09-08: the shipment is physically back with the seller and
    # there is nothing left to chase at the carrier or to action on the board.
    # It is NOT lost from the numbers -- performance.build() still counts it as
    # `rto_completed` in the carrier scorecards, which is where a completed
    # return actually belongs.
    #
    # A return still IN FLIGHT (RTO_INITIATED / RTO_IN_TRANSIT) stays, because
    # its closure can still be chased for restock and refund.
    if fused.canonical in (C.DELIVERED, C.CANCELLED,
                           C.RTO_INITIATED, C.RTO_IN_TRANSIT, C.RTO_DELIVERED):
        return None

    hours_since_scan: float | None = None
    if fused.at is not None:
        hours_since_scan = max(0.0, (now - fused.at).total_seconds() / 3600.0)

    overdue = days_overdue(ship.promised_date, now)

    # --- terminal-ish trouble -------------------------------------------------
    if fused.canonical == C.LOST_OR_DAMAGED:
        flags.append("LOST_SUSPECTED")
    # RTO_DELIVERED is handled by the early return above, so only in-flight
    # returns can reach here.
    if fused.canonical == C.DELIVERY_ATTEMPT_FAILED:
        flags.append("NDR_FAILED_ATTEMPT")
    if fused.canonical == C.ON_HOLD:
        flags.append("ON_HOLD")
    if fused.canonical == C.AWB_NOT_FOUND:
        # Not terminal: a fresh manifest is legitimately invisible for a while.
        if (ship.days_since_dispatch or 0) * 24 >= AWB_NOT_FOUND_ESCALATE_HOURS:
            flags.append("AWB_NOT_FOUND")
            notes.append("AWB never appeared at the carrier")

    # --- the promise ----------------------------------------------------------
    if overdue is not None and overdue > 0:
        flags.append("BREACHED")
    elif overdue is not None and -AT_RISK_WITHIN_DAYS <= overdue <= 0 \
            and fused.canonical not in (C.OUT_FOR_DELIVERY, C.DELIVERED):
        flags.append("AT_RISK")

    # --- movement -------------------------------------------------------------
    if hours_since_scan is not None:
        limit = (STUCK_OUT_FOR_DELIVERY_HOURS
                 if fused.canonical == C.OUT_FOR_DELIVERY
                 else STUCK_IN_TRANSIT_HOURS)
        if fused.canonical in (C.IN_TRANSIT, C.OUT_FOR_DELIVERY,
                               C.REACHED_DESTINATION_HUB, C.PICKED_UP) \
                and hours_since_scan > limit:
            flags.append("STUCK")
            notes.append(f"no movement for {hours_since_scan:.0f}h "
                         f"(limit {limit}h for {fused.canonical.value})")

    if fused.canonical in (C.MANIFESTED,) and ship.days_since_dispatch is not None \
            and ship.days_since_dispatch * 24 >= NO_PICKUP_HOURS:
        flags.append("NO_PICKUP")

    # --- data quality that blocks action -------------------------------------
    if is_declared_delay(ship.uniware_tracking_status):
        flags.append("CARRIER_DECLARED_DELAY")
    if fused.mismatch:
        flags.append("STATUS_MISMATCH")
        notes.append(f"Uniware says {fused.uniware_canonical.value}, "
                     f"carrier says {fused.lsp_canonical.value}")
    if ship.item_status == "CANCELLED" and fused.canonical in (
            C.IN_TRANSIT, C.OUT_FOR_DELIVERY, C.PICKED_UP,
            C.REACHED_DESTINATION_HUB):
        flags.append("CANCELLED_BUT_MOVING")
    if ship.cohort == "no_dispatch_date":
        flags.append("NO_DISPATCH_DATE")
    if not fused.mapped:
        flags.append("UNMAPPED_STATUS")

    spec = registry.get_spec(ship.adapter_id)
    if spec is not None and not spec.enabled:
        flags.append("LSP_BLOCKED")
        notes.append(spec.disabled_reason or "adapter disabled")
    elif ship.adapter_id == "unknown":
        flags.append("LSP_BLOCKED")
        notes.append(f"no adapter rule for courier {ship.courier_code!r}")

    if not flags:
        return None

    if ship.promise_source == "ASSUMED":
        notes.append(f"promised date assumed ({ship.promise_days}d default), "
                     "no SLA rule for this lane")

    flags = sorted(set(flags), key=lambda f: -SEVERITY.get(f, 0))

    # A loss SUPERSEDES the delivery-promise flags: a shipment the carrier has
    # lost was never going to arrive on time, so carrying BREACHED/AT_RISK/STUCK
    # alongside it is redundant noise. The promise is still recorded on the row.
    # (Returns used to supersede here too; they now leave the board entirely.)
    if "LOST_SUSPECTED" in flags:
        superseded = [f for f in ("BREACHED", "AT_RISK", "STUCK") if f in flags]
        if superseded:
            flags = [f for f in flags if f not in superseded]
            notes.append("superseded by the loss: "
                         + ", ".join(superseded))
    return Alert(
        awb=ship.awb, flags=flags,
        severity=max(SEVERITY.get(f, 0) for f in flags),
        primary_flag=flags[0],
        bucket=BUCKET.get(flags[0], "data_quality"),
        lsp=(spec.display_name if spec else ship.adapter_id),
        courier_code=ship.courier_code, facility_code=ship.facility_code,
        city=ship.city, pincode=ship.pincode, channel=ship.channel,
        payment_type=ship.payment_type,
        sale_order_codes=ship.sale_order_codes[:5], item_count=ship.item_count,
        status=fused.canonical.value, raw_status=fused.raw,
        status_source=fused.source,
        status_at=fused.at.isoformat() if fused.at else None,
        last_scan_location=(poll.current_location if poll else None),
        promised_date=ship.promised_date, promise_source=ship.promise_source,
        days_overdue=overdue, days_since_dispatch=ship.days_since_dispatch,
        hours_since_scan=(round(hours_since_scan, 1)
                          if hours_since_scan is not None else None),
        attempts=(poll.attempt_count if poll else None),
        ndr_reason=(poll.ndr_reason if poll else None),
        notes=notes,
    )


def evaluate_all(shipments: Iterable[Shipment],
                 polls: dict[str, TrackingResult] | None = None,
                 now: datetime | None = None) -> list[Alert]:
    now = now or now_ist()
    polls = polls or {}
    out: list[Alert] = []
    for ship in shipments:
        # `excluded` is as absent as `closed`. Skipping only "closed" here was
        # a real leak: the intake cohort kept excluded carriers out of the
        # coverage funnel's open set, yet 6 of them still reached the board
        # (5 in-house-fleet, 1 unmapped courier) because this loop never
        # consulted the cohort beyond "closed". Keep this in step with
        # coverage_funnel's definition of open.
        if ship.cohort in ("closed", "excluded"):
            continue
        alert = evaluate(ship, polls.get(ship.awb), now)
        if alert is not None:
            out.append(alert)
    # Most urgent first, then longest-overdue, so the top of the table is
    # always the thing to chase next.
    out.sort(key=lambda a: (-a.severity, -(a.days_overdue or 0)))
    return out


def summarise(alerts: list[Alert]) -> dict[str, Any]:
    from collections import Counter
    flat = Counter(f for a in alerts for f in a.flags)
    return {
        "alerts_total": len(alerts),
        "by_flag": dict(flat.most_common()),
        "by_primary_flag": dict(Counter(a.primary_flag for a in alerts).most_common()),
        "by_lsp": dict(Counter(a.lsp for a in alerts).most_common()),
        "by_facility": dict(Counter(a.facility_code for a in alerts).most_common()),
        "breached": sum(1 for a in alerts if "BREACHED" in a.flags),
        "at_risk": sum(1 for a in alerts if "AT_RISK" in a.flags),
        "stuck": sum(1 for a in alerts if "STUCK" in a.flags),
        "assumed_promise": sum(1 for a in alerts if a.promise_source == "ASSUMED"),
        "worst_overdue_days": max((a.days_overdue or 0 for a in alerts), default=0),
        "by_bucket": dict(Counter(a.bucket for a in alerts).most_common()),
    }
