"""Promised-delivery-date computation.

Ported (copied, not imported) from sla-remap-skill on 2026-09-07:
  - cutoff_tat_days, NDD_CUTOFF_HOUR=17, SDD_CUTOFF_HOUR=14
        <- scripts/pipeline_tat.py:60-72
  - parse_sla_days, load_rules, lsp_matches
        <- scripts/pipeline_recommend.py:113-195

Copied rather than imported for a structural reason: sla-remap-skill is not a
git repository and lives under OneDrive, so a cloud routine can never reach it,
and its warehouse_map does a filesystem-relative importlib load that would
force us to reproduce its directory layout anyway.

Two dates are produced per shipment, and keeping BOTH is the point:

  promised_delivery_date -- the customer promise. Cutoff-aware from order
      creation, per the (pincode, warehouse, lsp) rule.
  dispatch-anchored transit -- LSP accountability, measured from Dispatch Date.

That split is what separates "the warehouse dispatched late" from "the LSP is
slow", which is the single most useful distinction to have in hand during an
LSP escalation.

Lanes with no active rule fall back to ASSUMED_SLA_DAYS with
promise_source=ASSUMED, and are labelled as assumed everywhere they surface --
never silently counted as an on-time success or failure.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any

from .dates import IST, parse_dt

NDD_CUTOFF_HOUR = 17   # general / next-day pickup cutoff: 5pm
SDD_CUTOFF_HOUR = 14   # same-day pickup cutoff: 2pm

#: User decision (2026-09-07): lanes absent from SERVICEABILITYRULES_DP get a
#: flat 6-day promise. 6 is the modal SLACODE (STANDARD_6D, 95,736 of 164,811
#: rules). Known limitation: it understates lateness on fast intracity
#: smartlock lanes (~11,900 rows), so it is labelled as assumed and is meant to
#: be revisited per courier once real transit distributions are visible.
ASSUMED_SLA_DAYS = 6

STANDARD_DAYS_RE = re.compile(r"STANDARD_(\d+)D", re.IGNORECASE)
# SERVICEABILITYRULES_DP comes from Jarvis, which is VPN-gated -- this file
# is deliberately absent in this repo, so every promise falls through to the
# ASSUMED default. Wire the Jarvis pull in and drop the CSV here to upgrade
# promise_source from ASSUMED to RULES.
RULES_CSV = Path(__file__).resolve().parent / "reference" / "serviceability_rules_active.csv"


def parse_sla_days(slacode: str | None) -> int | None:
    """SLACODE -> promised day-count, or None if unrecognised.

    SFX_NDD (next-day) and Standard_SDD (same-day) are named promise types, not
    STANDARD_<n>D codes; a STANDARD-only regex silently dropped every rule using
    them (they surfaced as "no rule"). Preserved from the upstream fix.
    """
    if not slacode:
        return None
    code = slacode.strip().upper()
    if code == "STANDARD_SDD":
        return 0
    if code == "SFX_NDD":
        return 1
    m = STANDARD_DAYS_RE.search(code)
    return int(m.group(1)) if m else None


def cutoff_tat_days(created: datetime, delivered: datetime, cutoff_hour: int) -> int:
    """Calendar days from the EFFECTIVE pickup date to delivery.

    An order placed after the cutoff cannot be picked up that day, so its clock
    starts the next day. This is the real mechanic, user-confirmed upstream.
    """
    effective_pickup_date = created.date()
    if created.time() >= dtime(hour=cutoff_hour):
        effective_pickup_date += timedelta(days=1)
    return (delivered.date() - effective_pickup_date).days


def effective_pickup_date(created: datetime, cutoff_hour: int):
    d = created.date()
    if created.time() >= dtime(hour=cutoff_hour):
        d += timedelta(days=1)
    return d


def lsp_matches(observed_lsp: str, rule_lsp: str) -> bool:
    """Case-insensitive equality, plus the one audited special case.

    Uniware collapses DTDC's 15 `DTDC_RAFTAAR_<location>` providers to a bare
    `DTDC` courier, while the rules table keeps them specific. DTDC is the only
    family with this generic/specific split.
    """
    a = (observed_lsp or "").strip().upper()
    b = (rule_lsp or "").strip().upper()
    if not a or not b:
        return False
    if a == b:
        return True
    if a == "DTDC" and b.startswith("DTDC"):
        return True
    return False


@dataclass
class Promise:
    days: int
    source: str            # RULES | ASSUMED
    slacode: str | None
    cutoff_hour: int
    promised_date: Any = None      # datetime.date
    matched_lsp: str | None = None

    @property
    def assumed(self) -> bool:
        return self.source == "ASSUMED"


class SlaRules:
    """The active serviceability rules, indexed for lookup by lane."""

    def __init__(self, csv_path: Path | str = RULES_CSV) -> None:
        self.by_pincode: dict[str, list[dict[str, str]]] = {}
        self.by_city: dict[str, list[dict[str, str]]] = {}
        self.rows = 0
        self._load(Path(csv_path))

    def _load(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"SLA rules snapshot missing: {path}")
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                if (row.get("ISACTIVE") or "").strip().lower() in ("false", "0", "no"):
                    continue
                self.rows += 1
                pin = (row.get("PINCODE") or "").strip()
                city = (row.get("CITY") or "").strip().upper()
                if pin:
                    self.by_pincode.setdefault(pin, []).append(row)
                if city:
                    self.by_city.setdefault(city, []).append(row)

    def lookup(self, pincode: str | None, warehouse: str | None,
               lsp: str | None, city: str | None = None) -> tuple[int, str, str | None, str | None] | None:
        """Most specific match first: pincode+warehouse+lsp, then pincode+lsp,
        then pincode, then city+lsp. Returns (days, slacode, matched_lsp, grain)."""
        pin = (pincode or "").strip()
        wh = (warehouse or "").strip().upper()
        candidates = self.by_pincode.get(pin, [])

        # EVERY grain must match the LSP. A rule written for another carrier on
        # the same pincode is not this shipment's promise: inheriting it would
        # silently substitute a proxy value and quietly bypass the explicit
        # decision that uncovered lanes get ASSUMED_SLA_DAYS. So BD_SMARTLOCKS_*,
        # DELHIVERY_SPARES and HOLISOL_* -- absent from the rules table -- must
        # fall through to ASSUMED rather than borrow Shadowfax's SLA.
        for grain, pred in (
            ("pincode+warehouse+lsp",
             lambda r: (r.get("WAREHOUSE") or "").strip().upper() == wh
             and lsp_matches(lsp or "", r.get("LSPPARTNER") or "")),
            ("pincode+lsp",
             lambda r: lsp_matches(lsp or "", r.get("LSPPARTNER") or "")),
        ):
            for r in candidates:
                if pred(r):
                    days = parse_sla_days(r.get("SLACODE"))
                    if days is not None:
                        return days, (r.get("SLACODE") or ""), r.get("LSPPARTNER"), grain

        for r in self.by_city.get((city or "").strip().upper(), []):
            if lsp_matches(lsp or "", r.get("LSPPARTNER") or ""):
                days = parse_sla_days(r.get("SLACODE"))
                if days is not None:
                    return days, (r.get("SLACODE") or ""), r.get("LSPPARTNER"), "city+lsp"
        return None

    def promise(self, created: datetime | str | None, pincode: str | None,
                warehouse: str | None, lsp: str | None,
                city: str | None = None) -> Promise:
        """The customer-facing promised delivery date for one shipment."""
        hit = self.lookup(pincode, warehouse, lsp, city)
        if hit is not None:
            days, slacode, matched, _grain = hit
            source = "RULES"
        else:
            days, slacode, matched = ASSUMED_SLA_DAYS, None, None
            source = "ASSUMED"

        # A same-day promise is measured against the earlier SDD cutoff.
        cutoff = SDD_CUTOFF_HOUR if days == 0 else NDD_CUTOFF_HOUR
        created_dt = parse_dt(created) if not isinstance(created, datetime) else created
        promised = None
        if created_dt is not None:
            promised = effective_pickup_date(created_dt, cutoff) + timedelta(days=days)
        return Promise(days=days, source=source, slacode=slacode or None,
                       cutoff_hour=cutoff, promised_date=promised,
                       matched_lsp=matched)


def days_overdue(promised_date, now: datetime | None = None) -> int | None:
    """Days past the promise. Negative means still in time.

    Accepts a date, a datetime, or an ISO string, because the promise is a date
    but every source that carries it round-trips through a different type.
    """
    if promised_date is None:
        return None
    if isinstance(promised_date, str):
        promised_date = parse_dt(promised_date)
        if promised_date is None:
            return None
    if isinstance(promised_date, datetime):
        promised_date = promised_date.date()
    ref = (now or datetime.now(IST)).date()
    return (ref - promised_date).days
