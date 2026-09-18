"""Build the AWB watchlist from Uniware Sale Order rows.

The single most important transformation here is the collapse from ORDER-ITEM
grain to SHIPMENT grain. One AWB carries several order items -- measured at
5.4x on real data -- so tracking at item grain would multiply every LSP call
by five and make "how many shipments are late" unanswerable.

Scope is the four channels that actually hold spares, refresh kits and RO
purifiers. `CUSTOM_UC_D2C_RO` alone is purifiers only; the spares and refresh
kits live in `CUSTOM_UC_APP`.

Uniware's own `Shipping Tracking Status` is carried through, because a measured
~84% of open in-scope AWBs already have it. That makes it a free first-pass
status and a cross-check -- and the AWBs where it is ABSENT are precisely the
population that has to be asked of the LSP.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from . import uniware_status
from .dates import IST, now_ist, parse_dt
from .lsp import registry
from .sla import SlaRules
from .uniware_tz import to_ist

#: The channels that hold spares + refresh kits + RO purifiers.
IN_SCOPE_CHANNELS: frozenset[str] = frozenset({
    "CUSTOM_UC_APP",         # spares + refresh kits (the bulk)
    "CUSTOM_UC_D2C_RO",      # RO purifiers
    "CUSTOM_UC_MANUAL",      # manual / CS-created, incl. refresh kits
    "CUSTOM_UC_D2C_STORES",  # store D2C, same goods
})

#: Order-item statuses that mean the shipment is settled and needs no tracking.
CLOSED_ITEM_STATUS: frozenset[str] = frozenset({"DELIVERED", "CANCELLED"})

#: Adapters whose volume never enters the dashboard at all. Loaded from
#: config.json so there is one place to change it. See that file's comment for
#: why DTDC is deliberately not among them.
def _excluded_adapters() -> frozenset[str]:
    try:
        cfg = json.loads((Path(__file__).resolve().parent / "config.json")
                         .read_text(encoding="utf-8"))
        return frozenset(cfg.get("excluded_adapters", {}).get("adapters", []))
    except Exception:
        return frozenset()


EXCLUDED_ADAPTERS: frozenset[str] = _excluded_adapters()


def _live_polled_adapters() -> frozenset[str]:
    """Carriers we poll directly; Uniware must not answer for them.

    See config.json -> live_polled_adapters for the measurement behind it.
    """
    try:
        cfg = json.loads((Path(__file__).resolve().parent / "config.json")
                         .read_text(encoding="utf-8"))
        return frozenset(cfg.get("live_polled_adapters", {}).get("adapters", []))
    except Exception:
        # Failing OPEN (empty set) keeps Uniware as a fallback, which is the
        # behaviour that existed before this was added -- a degraded board
        # beats a blank one if the config is ever unreadable.
        return frozenset()


LIVE_POLLED_ADAPTERS: frozenset[str] = _live_polled_adapters()

#: How far past dispatch an AWB stays on the live watchlist.
LIVE_DISPATCH_HORIZON_DAYS = 15
#: Beyond the horizon but still not delivered = the stale backlog cohort.
BACKLOG_HORIZON_DAYS = 60

BLANKS = {"", "-", "NA", "N/A", "0", "NULL", "NONE"}


def _g(row: dict[str, Any], key: str) -> str:
    return (row.get(key) or "").strip()


def clean_awb(value: str | None) -> str:
    awb = (value or "").strip().upper()
    return "" if awb in BLANKS else awb


def recency_key(row: dict[str, Any]) -> tuple:
    """Latest-wins ordering for de-duplication.

    Preserves the upstream tie-break: a DELIVERED row beats an undelivered one,
    then later `Updated`, then later file. Getting this wrong silently lost
    ~6,800 rows upstream, so it is reproduced rather than re-invented.
    """
    delivered = 1 if _g(row, "Sale Order Item Status") == "DELIVERED" else 0
    updated = parse_dt(_g(row, "Updated")) or datetime.min.replace(tzinfo=IST)
    rank = int(row.get("_file_rank") or 0)
    return (delivered, updated, rank)


def dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (Sale Order Code, Sale Order Item Code), latest wins."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (_g(row, "Sale Order Code"), _g(row, "Sale Order Item Code"))
        current = best.get(key)
        if current is None or recency_key(row) > recency_key(current):
            best[key] = row
    return list(best.values())


@dataclass
class Shipment:
    awb: str
    courier_code: str
    adapter_id: str
    sale_order_codes: list[str] = field(default_factory=list)
    sale_order_item_codes: list[str] = field(default_factory=list)
    item_count: int = 0
    channel: str = ""
    payment_type: str = ""    # COD | Prepaid | "" when the export predates the column
    facility_code: str = ""
    city: str = ""
    pincode: str = ""
    shipping_provider: str = ""
    created_at: str | None = None
    dispatch_date: str | None = None
    delivery_time: str | None = None
    item_status: str = ""
    # Uniware's own view of LSP state -- free first pass and cross-check.
    uniware_tracking_status: str = ""
    uniware_courier_status: str = ""
    package_status_code: str = ""
    # Promise
    promised_date: str | None = None
    promise_days: int | None = None
    promise_source: str = ""
    promise_slacode: str | None = None
    # Bookkeeping
    days_since_dispatch: int | None = None
    cohort: str = ""          # live | backlog | no_dispatch_date | closed
    needs_lsp_poll: bool = True
    awb_pattern_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def in_scope(row: dict[str, Any]) -> bool:
    return _g(row, "Channel Name") in IN_SCOPE_CHANNELS


def payment_type_of(items: list[dict[str, Any]]) -> str:
    """COD | Prepaid | "" for a shipment's order-item rows.

    Uniware's `COD` column is a per-sale-order 0/1 flag, and one AWB can carry
    items from more than one order, so ANY COD item makes the shipment COD --
    cash still has to be collected at the door.

    A blank stays blank rather than defaulting to Prepaid. Rows exported before
    the column was requested (2026-09-11) carry no COD field at all, and calling
    those "Prepaid" would be inventing a fact about money.
    """
    seen = False
    for row in items:
        v = _g(row, "COD").strip()
        if not v:
            continue
        seen = True
        if v not in ("0", "false", "FALSE", "No", "NO"):
            return "COD"
    return "Prepaid" if seen else ""


def build_shipments(rows: Iterable[dict[str, Any]], rules: SlaRules | None = None,
                    now: datetime | None = None,
                    dq: Any | None = None) -> tuple[list[Shipment], dict[str, Any]]:
    """Collapse de-duplicated order-item rows into shipment-grain records."""
    now = now or now_ist()
    rules = rules or SlaRules()
    stats: dict[str, Any] = {
        "rows_in": 0, "rows_in_scope": 0, "rows_no_awb": 0,
        "rows_closed": 0, "shipments": 0,
    }

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        stats["rows_in"] += 1
        if not in_scope(row):
            continue
        stats["rows_in_scope"] += 1
        awb = clean_awb(_g(row, "Tracking Number"))
        if not awb:
            stats["rows_no_awb"] += 1
            if dq is not None and _g(row, "Sale Order Item Status") not in ("FULFILLABLE", "CREATED"):
                dq.missing_awb(_g(row, "Shipping Courier"), _g(row, "Sale Order Code"))
            continue
        grouped.setdefault(awb, []).append(row)

    shipments: list[Shipment] = []
    for awb, items in grouped.items():
        # The most advanced item drives the shipment's state.
        lead = max(items, key=recency_key)
        courier = _g(lead, "Shipping Courier")
        res = registry.resolve(courier)
        if res.is_unknown and dq is not None:
            dq.unmapped_courier(courier, awb)

        item_status = _g(lead, "Sale Order Item Status")
        dispatch_raw = _g(lead, "Dispatch Date")
        dispatch_dt = to_ist(dispatch_raw, res.adapter_id)
        days_since = (now.date() - dispatch_dt.date()).days if dispatch_dt else None

        pkg_code = _g(lead, "Shipping Package Status Code")
        track_status = _g(lead, "Shipping Tracking Status")

        if res.adapter_id in EXCLUDED_ADAPTERS:
            # Out of scope entirely: in-house fleet, no courier assigned, Porter,
            # or a courier code with no adapter rule. Cohorting here is what keeps
            # them out of alerts, scorecards and lanes in one move rather than
            # needing a filter at each surface.
            cohort = "excluded"
        elif item_status in CLOSED_ITEM_STATUS:
            cohort = "closed"
        elif uniware_status.is_return(track_status, pkg_code):
            # A return is settled for this board's purposes (user decision
            # 2026-09-09). Cohorting it CLOSED at intake is what stops the
            # rolling 45-day export re-adding it every single morning -- the
            # item status stays DISPATCHED on a return, so CLOSED_ITEM_STATUS
            # alone never catches one.
            cohort = "closed"
        elif days_since is None:
            cohort = "no_dispatch_date"
        elif days_since <= LIVE_DISPATCH_HORIZON_DAYS:
            cohort = "live"
        elif days_since <= BACKLOG_HORIZON_DAYS:
            cohort = "backlog"
        else:
            cohort = "aged_out"

        promise = rules.promise(
            created=_g(lead, "Created") or _g(lead, "Order Date as dd/mm/yyyy hh:MM:ss"),
            pincode=_g(lead, "Shipping Address Pincode"),
            warehouse=_g(lead, "Facility Code"),
            lsp=courier, city=_g(lead, "Shipping Address City"))

        pattern_ok = registry.validate_awb(awb, res.adapter_id)
        if pattern_ok is False and dq is not None:
            dq.awb_pattern_mismatch(res.adapter_id, awb, courier)

        uni_status = _g(lead, "Shipping Tracking Status")
        ship = Shipment(
            awb=awb, courier_code=courier, adapter_id=res.adapter_id,
            sale_order_codes=sorted({_g(i, "Sale Order Code") for i in items if _g(i, "Sale Order Code")}),
            sale_order_item_codes=sorted({_g(i, "Sale Order Item Code") for i in items if _g(i, "Sale Order Item Code")}),
            item_count=len(items),
            channel=_g(lead, "Channel Name"),
            payment_type=payment_type_of(items),
            facility_code=_g(lead, "Facility Code"),
            city=_g(lead, "Shipping Address City"),
            pincode=_g(lead, "Shipping Address Pincode"),
            shipping_provider=_g(lead, "Shipping provider"),
            created_at=_g(lead, "Created") or None,
            dispatch_date=dispatch_raw or None,
            delivery_time=_g(lead, "Delivery Time") or None,
            item_status=item_status,
            uniware_tracking_status=uni_status,
            uniware_courier_status=_g(lead, "Shipping Courier Status"),
            package_status_code=_g(lead, "Shipping Package Status Code"),
            promised_date=promise.promised_date.isoformat() if promise.promised_date else None,
            promise_days=promise.days,
            promise_source=promise.source,
            promise_slacode=promise.slacode,
            days_since_dispatch=days_since,
            cohort=cohort,
            # Uniware already knows the LSP state for most open AWBs; those
            # need a poll only for freshness, not for a first answer.
            needs_lsp_poll=(cohort in ("live", "backlog", "no_dispatch_date")),
            awb_pattern_ok=pattern_ok,
        )
        shipments.append(ship)

    stats["shipments"] = len(shipments)
    stats["rows_closed"] = sum(1 for s in shipments if s.cohort == "closed")
    return shipments, stats


def summarise(shipments: list[Shipment]) -> dict[str, Any]:
    """Counts the dashboard's coverage funnel and the run summary both need."""
    from collections import Counter
    by_cohort = Counter(s.cohort for s in shipments)
    by_adapter = Counter(s.adapter_id for s in shipments)
    open_ships = [s for s in shipments if s.cohort in ("live", "backlog", "no_dispatch_date")]
    trackable = [s for s in open_ships if s.adapter_id not in ("not_trackable", "unknown")]
    enabled = [s for s in trackable if (registry.get_spec(s.adapter_id) or None)
               and registry.get_spec(s.adapter_id).enabled]
    return {
        "shipments_total": len(shipments),
        "by_cohort": dict(by_cohort),
        "by_adapter": dict(by_adapter),
        "open_total": len(open_ships),
        "open_live": by_cohort.get("live", 0),
        "open_backlog": by_cohort.get("backlog", 0),
        "open_no_dispatch_date": by_cohort.get("no_dispatch_date", 0),
        "open_trackable": len(trackable),
        "open_pollable_enabled_adapter": len(enabled),
        "open_with_uniware_status": sum(1 for s in open_ships if s.uniware_tracking_status),
        "open_without_uniware_status": sum(1 for s in open_ships if not s.uniware_tracking_status),
        "promise_assumed": sum(1 for s in open_ships if s.promise_source == "ASSUMED"),
        "item_rows_to_shipment_ratio": (
            round(sum(s.item_count for s in shipments) / len(shipments), 2)
            if shipments else 0),
    }


def save(shipments: list[Shipment], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([s.to_dict() for s in shipments], indent=1,
                            default=str), encoding="utf-8")
    return p
