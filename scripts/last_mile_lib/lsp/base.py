"""Canonical tracking model and the adapter contract.

Every LSP module exposes exactly three module-level names -- ADAPTER_ID, SPEC
and track() -- so adding a carrier is one new file plus one registry rule, with
no change to the core.

Design rule that everything here serves: a raw LSP status we do not recognise
must SURFACE as a data-quality item, never be quietly folded into UNKNOWN.
A wrong guess silently corrupts on-time %, which is the one number this whole
system exists to report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, Sequence


class CanonicalStatus(str, Enum):
    """A deliberate subset of Uniware's own tracking enum, so our output joins
    cleanly to the system of record."""

    MANIFESTED = "MANIFESTED"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    REACHED_DESTINATION_HUB = "REACHED_DESTINATION_HUB"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    DELIVERY_ATTEMPT_FAILED = "DELIVERY_ATTEMPT_FAILED"  # NDR
    ON_HOLD = "ON_HOLD"
    LOST_OR_DAMAGED = "LOST_OR_DAMAGED"
    CANCELLED = "CANCELLED"
    RTO_INITIATED = "RTO_INITIATED"
    RTO_IN_TRANSIT = "RTO_IN_TRANSIT"
    RTO_DELIVERED = "RTO_DELIVERED"
    AWB_NOT_FOUND = "AWB_NOT_FOUND"
    NOT_TRACKABLE = "NOT_TRACKABLE"
    UNKNOWN = "UNKNOWN"  # always paired with a DQ item


#: Reaching one of these means stop polling this AWB, permanently.
#:
#: The whole RETURN family is terminal by user decision 2026-09-09: "add RTO in
#: transit, Return, Return expected and RTO as well -- we don't need to track
#: them once they are marked." A shipment that has turned around is not going to
#: be delivered to the customer, so there is nothing left for this board to
#: chase. Returns are NOT lost from the numbers: performance.build() still
#: counts them per carrier as `rto_in_flight` / `rto_completed` and reports
#: `rto_rate_pct`, which is where a return actually belongs.
#:
#: RTO_IN_TRANSIT being terminal is a deliberate reversal -- the Delhivery
#: adapter used to map RETURNED to it precisely to keep returns tracked.
TERMINAL: frozenset[CanonicalStatus] = frozenset({
    CanonicalStatus.DELIVERED,
    CanonicalStatus.CANCELLED,
    CanonicalStatus.LOST_OR_DAMAGED,
    CanonicalStatus.RTO_INITIATED,
    CanonicalStatus.RTO_IN_TRANSIT,
    CanonicalStatus.RTO_DELIVERED,
})

#: The return subset of TERMINAL. Settled the moment it is seen, with no
#: confirmation re-poll: that re-poll exists to catch a FALSE `DELIVERED`, which
#: would wrongly drop a live shipment off the board. A return marking makes no
#: claim of successful delivery, so there is nothing to double-check.
RETURN_TERMINAL: frozenset[CanonicalStatus] = frozenset({
    CanonicalStatus.RTO_INITIATED,
    CanonicalStatus.RTO_IN_TRANSIT,
    CanonicalStatus.RTO_DELIVERED,
})

#: AWB_NOT_FOUND is deliberately NOT terminal -- a freshly manifested shipment
#: is legitimately invisible at the LSP for a while. It escalates on age instead.


class FetchOutcome(str, Enum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    NOT_TRACKABLE = "NOT_TRACKABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BLOCKED = "BLOCKED"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    PARSE_ERROR = "PARSE_ERROR"
    SKIPPED_BUDGET = "SKIPPED_BUDGET"


@dataclass(frozen=True)
class TrackEvent:
    """One scan line, as the LSP reported it."""

    at: datetime | None
    raw_status: str
    canonical: CanonicalStatus
    location: str | None = None
    remark: str | None = None
    raw_status_mapped: bool = True


@dataclass
class TrackingResult:
    awb: str
    adapter_id: str
    courier_code: str
    fetched_at: datetime
    outcome: FetchOutcome
    canonical_status: CanonicalStatus
    raw_status: str | None = None        # verbatim -- never normalised away
    raw_status_mapped: bool = True       # False -> mandatory DQ item
    status_at: datetime | None = None
    events: tuple[TrackEvent, ...] = ()
    expected_delivery: datetime | None = None   # the LSP's OWN promise, when given
    attempt_count: int | None = None
    ndr_reason: str | None = None
    current_location: str | None = None
    destination: str | None = None
    recipient: str | None = None
    http_status: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    raw_payload: Any | None = None

    @property
    def is_terminal(self) -> bool:
        return self.canonical_status in TERMINAL

    def summary(self) -> dict[str, Any]:
        return {
            "awb": self.awb, "lsp": self.adapter_id, "outcome": self.outcome.value,
            "status": self.canonical_status.value, "raw": self.raw_status,
            "mapped": self.raw_status_mapped,
            "at": self.status_at.isoformat() if self.status_at else None,
            "events": len(self.events), "http": self.http_status,
            "ms": self.latency_ms, "error": self.error,
        }


@dataclass(frozen=True)
class AdapterSpec:
    adapter_id: str
    display_name: str
    mode: str                      # public_json | public_html | official_api | not_trackable
    enabled: bool = True
    requires_credentials: bool = False
    batch_size: int = 1            # >1 only where the API accepts many AWBs
    max_concurrency: int = 2
    min_interval_s: float = 0.5
    max_awbs_per_run: int | None = None
    timeout_connect_s: float = 10.0
    timeout_read_s: float = 30.0
    awb_pattern: str | None = None
    #: Substrings that mean "we are being blocked" regardless of status code.
    block_signatures: tuple[str, ...] = ()
    #: Why this adapter is disabled, surfaced on the dashboard as a named gap.
    disabled_reason: str | None = None
    notes: str = ""


@dataclass
class FetchContext:
    """Everything an adapter needs from the runtime, injected rather than global."""

    now: datetime
    http: Any                      # awb_tracker.lsp.http.HttpClient
    secrets: dict[str, str] = field(default_factory=dict)
    deadline: datetime | None = None
    dq: Any | None = None          # DQ sink; see status_map.DQSink
    dry_run: bool = False
    #: Per-run override of AdapterSpec.max_awbs_per_run, by adapter id.
    #: This is what makes hourly_track --cap mean what its help says.
    #: It lives here rather than mutating SPEC, which is module level
    #: and shared -- mutating it would leak one run's cap into every
    #: later run in the process. Absent an entry, the adapter ceiling
    #: still applies.
    max_awbs_override: dict[str, int] = field(default_factory=dict)

    def out_of_budget(self) -> bool:
        """Whether the run's wall clock has expired.

        Compares against the REAL current time, not `self.now`. `self.now` is
        the run's fixed start stamp -- deliberately fixed, so every alert in a
        run is evaluated against one consistent moment -- so comparing it to
        the deadline made this permanently False and the wall-clock budget
        never fired. A full-scale run overran its 20-minute cap and had to be
        killed by hand.
        """
        if self.deadline is None:
            return False
        from ..dates import now_ist
        return now_ist() >= self.deadline


class Adapter(Protocol):
    ADAPTER_ID: str
    SPEC: AdapterSpec

    def track(self, awbs: Sequence[str], ctx: FetchContext) -> list[TrackingResult]:
        ...


def blocked_result(awb: str, spec: AdapterSpec, courier: str,
                   now: datetime, outcome: FetchOutcome,
                   status: CanonicalStatus, error: str) -> TrackingResult:
    """Uniform result for an adapter that cannot even try (disabled/no creds).

    Deliberately returned rather than omitted, so blocked volume shows on the
    dashboard as a quantified gap instead of silently vanishing.
    """
    return TrackingResult(
        awb=awb, adapter_id=spec.adapter_id, courier_code=courier,
        fetched_at=now, outcome=outcome, canonical_status=status, error=error,
    )
