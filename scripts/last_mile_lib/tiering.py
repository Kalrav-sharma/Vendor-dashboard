"""Poll scheduling: which AWBs to ask a carrier about on this run.

The run is hourly, but no AWB is polled hourly. That distinction is what makes
the cadence affordable: alerts stay fresh to the hour while real traffic sits
around 0.3 requests/second across four carriers.

  T0 URGENT   60 min   out for delivery, failed attempt, or within 1 day
                       before / 3 days after the promise -- the window where
                       chasing the carrier still changes the outcome
  T1 ACTIVE    3 h     normal in transit / picked up
  T2 EARLY    12 h     manifested with no pickup scan; chronic breach still
                       inside the dispatch window
  T3 STALE    24 h     chronic breach, no movement for 5+ days, persistent
                       not-found, on hold
  T4 SETTLED  never    delivered, returned to seller, cancelled, lost

Measured on the first real run: this split puts 1,055 shipments on hourly and
4,502 on daily. Marking every late shipment urgent instead put 5,564 of 6,377
on hourly, which is not tiering and would have earned a rate-limit.

Three independent guarantees that a settled AWB is never polled again:
  1. a terminal status sets `terminal`, and the selection query excludes it, so
     terminal rows are not merely deprioritised but unselectable;
  2. one confirmation re-poll 24h after the first DELIVERED, then frozen for
     good -- this catches a premature or false delivered scan without leaving
     an open-ended poll running;
  3. the daily intake left-joins against this state, so a rolling 45-day
     re-export cannot resurrect an AWB that already settled. Without that last
     one, every delivered shipment would come back on every single run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .dates import now_ist, parse_dt
from .lsp.base import RETURN_TERMINAL, TERMINAL, CanonicalStatus as C
from .lsp.base import FetchOutcome, TrackingResult

TIER_INTERVAL_HOURS: dict[str, float] = {
    "T0_URGENT": 1.0,
    "T1_ACTIVE": 3.0,
    "T2_EARLY": 12.0,
    "T3_STALE": 24.0,
}

STALE_AFTER_DAYS = 5
#: How long after the promised date a breach still counts as "act now". Past
#: this it is chronic and moves to a slower tier -- see classify().
FRESH_BREACH_DAYS = 3
CONFIRM_DELIVERED_AFTER_HOURS = 24


@dataclass
class PollRecord:
    awb: str
    tier: str = "T1_ACTIVE"
    last_polled_at: str | None = None
    next_poll_at: str | None = None
    last_status: str | None = None
    last_status_at: str | None = None
    last_change_at: str | None = None
    poll_count: int = 0
    consecutive_failures: int = 0
    terminal: bool = False
    #: Set when the first DELIVERED lands; the confirmation re-poll is due then.
    delivered_first_seen_at: str | None = None
    confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PollState:
    def __init__(self, path: str | Path = "state/poll_state.json",
                 preloaded: dict[str, dict] | None = None) -> None:
        self.path = Path(path)
        self.records: dict[str, PollRecord] = {}
        if preloaded is not None:
            # Cloud runs are stateless: the state arrives from the artifact
            # database rather than from disk. Unknown keys are dropped so a
            # schema change in either direction cannot break a run.
            fields = set(PollRecord.__dataclass_fields__)
            for awb, row in preloaded.items():
                try:
                    self.records[awb] = PollRecord(
                        **{k: v for k, v in row.items() if k in fields})
                except Exception:
                    continue
        else:
            self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for awb, d in raw.items():
                self.records[awb] = PollRecord(**d)
        except Exception:
            # Corrupt state must not take down a run; worst case we re-poll.
            self.records = {}

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({k: v.to_dict() for k, v in self.records.items()},
                       separators=(",", ":")), encoding="utf-8")
        return self.path

    def get(self, awb: str) -> PollRecord:
        rec = self.records.get(awb)
        if rec is None:
            rec = PollRecord(awb=awb)
            self.records[awb] = rec
        return rec

    def is_settled(self, awb: str) -> bool:
        rec = self.records.get(awb)
        return bool(rec and rec.terminal and rec.confirmed)

    # ------------------------------------------------------------- tiering
    @staticmethod
    def classify(ship: Any, status: C | None, now: datetime) -> str:
        """Assign a tier from the shipment and its last known status.

        Urgency means "the outcome can still change materially soon", NOT
        merely "this is late". Treating every late shipment as urgent put 5,564
        of 6,377 shipments in T0 on the first real run -- 87% hourly, which is
        not tiering at all and would have earned a rate-limit. A shipment 20
        days past its promise is chronic: checking it hourly changes nothing,
        so it drops to a slower tier until it moves again.
        """
        from .sla import days_overdue

        overdue = days_overdue(getattr(ship, "promised_date", None), now)
        days = getattr(ship, "days_since_dispatch", None)

        # Out for delivery or a failed attempt: the outcome is being decided
        # today, so this is where hourly freshness actually buys something.
        if status in (C.OUT_FOR_DELIVERY, C.DELIVERY_ATTEMPT_FAILED):
            return "T0_URGENT"

        # About to breach, or freshly breached: intervention still helps.
        if overdue is not None and -1 <= overdue <= FRESH_BREACH_DAYS:
            return "T0_URGENT"

        # Chronic breach. Keep watching, but not every hour.
        if overdue is not None and overdue > FRESH_BREACH_DAYS:
            if days is not None and days >= STALE_AFTER_DAYS:
                return "T3_STALE"
            return "T2_EARLY"

        if status in (C.ON_HOLD, C.AWB_NOT_FOUND):
            return "T3_STALE"
        if days is not None and days >= STALE_AFTER_DAYS \
                and status in (C.IN_TRANSIT, C.PICKED_UP, None):
            return "T3_STALE"
        if status in (C.MANIFESTED, None):
            return "T2_EARLY"
        return "T1_ACTIVE"

    def due(self, shipments: Iterable[Any], statuses: dict[str, C | None],
            now: datetime | None = None, limit: int | None = None
            ) -> tuple[list[Any], dict[str, int]]:
        """Shipments whose next poll is due, most urgent first.

        Selection deliberately excludes settled AWBs outright rather than
        sorting them last.
        """
        now = now or now_ist()
        picked: list[tuple[float, Any]] = []
        tally: dict[str, int] = {"settled_skipped": 0, "not_due": 0}

        for ship in shipments:
            awb = ship.awb
            rec = self.records.get(awb)

            if rec and rec.terminal:
                # Confirmation re-poll: exactly one, 24h after first DELIVERED.
                if rec.confirmed:
                    tally["settled_skipped"] += 1
                    continue
                first = parse_dt(rec.delivered_first_seen_at)
                if first is None or (now - first) < timedelta(hours=CONFIRM_DELIVERED_AFTER_HOURS):
                    tally["not_due"] += 1
                    continue
                tier = "T0_URGENT"
            else:
                tier = self.classify(ship, statuses.get(awb), now)

            if rec and rec.next_poll_at:
                nxt = parse_dt(rec.next_poll_at)
                if nxt is not None and now < nxt:
                    tally["not_due"] += 1
                    continue

            tally[tier] = tally.get(tier, 0) + 1
            rank = {"T0_URGENT": 0, "T1_ACTIVE": 1, "T2_EARLY": 2, "T3_STALE": 3}[tier]
            last = parse_dt(rec.last_polled_at) if rec and rec.last_polled_at else None
            age = -(now - last).total_seconds() if last else -1e12  # oldest first
            picked.append((rank * 1e13 + age, ship))
            self.get(awb).tier = tier

        picked.sort(key=lambda t: t[0])
        out = [s for _, s in picked]
        if limit is not None and len(out) > limit:
            tally["deferred_over_limit"] = len(out) - limit
            out = out[:limit]
        return out, tally

    # -------------------------------------------------------------- record
    def record(self, result: TrackingResult, now: datetime | None = None) -> None:
        """Fold one poll result into the state."""
        now = now or now_ist()
        rec = self.get(result.awb)
        rec.last_polled_at = now.isoformat()
        rec.poll_count += 1

        if result.outcome in (FetchOutcome.OK, FetchOutcome.NOT_FOUND,
                              FetchOutcome.NOT_TRACKABLE):
            rec.consecutive_failures = 0
        else:
            rec.consecutive_failures += 1

        if result.outcome == FetchOutcome.OK:
            new_status = result.canonical_status.value
            if new_status != rec.last_status:
                rec.last_change_at = now.isoformat()
            rec.last_status = new_status
            rec.last_status_at = (result.status_at.isoformat()
                                  if result.status_at else None)

            if result.canonical_status in RETURN_TERMINAL:
                # Settled on sight, no confirmation re-poll. The re-poll guards
                # against a FALSE `DELIVERED` dropping a live shipment; a return
                # claims no delivery, so there is nothing to double-check.
                rec.terminal = True
                rec.confirmed = True
                rec.next_poll_at = None
                return

            if result.canonical_status in TERMINAL:
                if not rec.terminal:
                    rec.terminal = True
                    rec.delivered_first_seen_at = now.isoformat()
                    # First sighting: schedule the single confirmation re-poll.
                    rec.next_poll_at = (now + timedelta(
                        hours=CONFIRM_DELIVERED_AFTER_HOURS)).isoformat()
                    return
                # This IS the confirmation. Freeze it for good.
                rec.confirmed = True
                rec.next_poll_at = None
                return

        interval = TIER_INTERVAL_HOURS.get(rec.tier, 3.0)
        if rec.consecutive_failures:
            # Back off a persistently failing AWB rather than retrying it hourly.
            interval = min(24.0, interval * (2 ** min(rec.consecutive_failures, 3)))
        rec.next_poll_at = (now + timedelta(hours=interval)).isoformat()

    def settled_awbs(self) -> set[str]:
        """For the daily intake's left-join, so a re-export cannot resurrect
        an AWB that already settled."""
        return {a for a, r in self.records.items() if r.terminal and r.confirmed}

    def prune(self, keep_awbs: set[str]) -> int:
        """Drop records for AWBs no longer in any window."""
        gone = [a for a in self.records if a not in keep_awbs]
        for a in gone:
            del self.records[a]
        return len(gone)

    def stats(self) -> dict[str, Any]:
        from collections import Counter
        return {
            "tracked_awbs": len(self.records),
            "terminal": sum(1 for r in self.records.values() if r.terminal),
            "confirmed_settled": sum(1 for r in self.records.values() if r.confirmed),
            "by_tier": dict(Counter(r.tier for r in self.records.values())),
            "with_failures": sum(1 for r in self.records.values()
                                 if r.consecutive_failures),
        }
