"""Blue Dart adapter -- verified live 2026-09-07 against ground truth.

    GET https://www.bluedart.com/web/guest/trackdartresultthirdparty
        ?trackFor=0&trackNo={awb}

`trackFor=0` means "waybill number". This third-party result page is the only
captcha-free path -- www.bluedart.com/tracking itself is a Liferay portlet
gated by a captcha.

Verified on AWB 77117234482 (Uniware said delivered 2026-08-14 16:37; Blue Dart
said 14 Aug 2026 16:37 -- exact match):

  summary table  : rows of 2 cells, key/value --
                   Waybill No | Pickup Date | From | To | Status |
                   Date of Delivery | Time of Delivery | Recipient | Reference No
  scan table     : title row, then header (Location, Details, Date, Time *),
                   then 4-cell rows, newest first. `Details` is the raw status
                   and carries real NDR reasons, e.g.
                   "Delivery Attempted-Premises Closed".

NOT-FOUND DETECTION, the trap: the strings "Records Not Found" and "no
information on the Waybill" exist in the page as a HIDDEN TEMPLATE even on a
successful response. Detecting them textually gives a false negative on every
good result. So absence is determined structurally: no `Waybill No` value in
the summary table.

The response is ~95 KB per AWB, so `max_awbs_per_run` bounds bandwidth.
"""
from __future__ import annotations

import re
from typing import Sequence

from ..dates import parse_date_time_pair, parse_dt
from ._loop import run_loop
from .base import (AdapterSpec, CanonicalStatus, FetchContext, FetchOutcome,
                   TrackEvent, TrackingResult)

ADAPTER_ID = "bluedart"
API = ("https://www.bluedart.com/web/guest/trackdartresultthirdparty"
       "?trackFor=0&trackNo={awb}")

SPEC = AdapterSpec(
    adapter_id=ADAPTER_ID,
    display_name="Blue Dart",
    mode="public_html",
    enabled=True,
    max_concurrency=2,
    min_interval_s=1.0,
    max_awbs_per_run=400,          # ~95 KB per AWB; bound the bandwidth
    timeout_read_s=45.0,
    awb_pattern=r"^\d{11}$",
    notes="Captcha-free third-party result page. Migrate to apigateway when creds land.",
)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_TABLE = re.compile(r'<table[^>]*class="[^"]*table-bordered[^"]*"[^>]*>.*?</table>',
                    re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.S | re.I)


def _clean(html: str) -> str:
    return _WS.sub(" ", _TAG.sub("", html)).replace("&nbsp;", " ").strip()


def _rows(table_html: str) -> list[list[str]]:
    return [[_clean(c) for c in _CELL.findall(row)] for row in _ROW.findall(table_html)]


#: `Status` / scan `Details` -> canonical, matched case-insensitively by
#: substring so Blue Dart's long phrasings resolve.
PHRASE_MAP: tuple[tuple[str, CanonicalStatus], ...] = (
    # "undelivered" MUST be tested before "delivered": classify() is
    # first-match-wins on a SUBSTRING, and "undelivered" contains "delivered".
    # With the old ordering every failed delivery was classified DELIVERED --
    # a TERMINAL status -- so the shipment silently left the board as though it
    # had arrived. Caught 2026-09-14 by test_existing_mapping_not_shadowed.
    # Any negative phrase that embeds a positive one belongs up here.
    ("undelivered", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("not delivered", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("unable to deliver", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("shipment delivered", CanonicalStatus.DELIVERED),
    ("delivered", CanonicalStatus.DELIVERED),
    ("out for delivery", CanonicalStatus.OUT_FOR_DELIVERY),
    ("delivery attempted", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("entry restricted for delivery", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    # Observed live 2026-09-08. Blue Dart words its NDR reasons as consignee
    # problems rather than using the word "undelivered", so these arrived as
    # UNMAPPED (18 of 40 polls) until they were mined from a real run.
    ("consignee refused", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("consignee not available", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("no such consignee", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("address incomplete", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("address incorrect", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    # A wrong pincode needs someone to correct the address before the shipment
    # can move, so it is held rather than merely in transit.
    ("wrong pincode", CanonicalStatus.ON_HOLD),
    # Carrier-declared delays: still moving, so not terminal and not an NDR.
    ("delivery delayed", CanonicalStatus.IN_TRANSIT),
    ("network delay", CanonicalStatus.IN_TRANSIT),
    ("redirected", CanonicalStatus.IN_TRANSIT),
    ("returned to shipper", CanonicalStatus.RTO_IN_TRANSIT),
    ("rto", CanonicalStatus.RTO_IN_TRANSIT),
    ("shipment held", CanonicalStatus.ON_HOLD),
    ("on hold", CanonicalStatus.ON_HOLD),
    # ---- mined from the uncapped sweep, 2026-09-14 (1,102 occurrences) ----
    # Ordering matters: these sit ABOVE the generic movement phrases below so a
    # specific reason is not swallowed by "in transit"/"received at".

    # Attempted and failed. All consignee- or address-caused, so they belong in
    # the NDR workflow rather than looking like ordinary transit.
    ("office closed", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("out of delivery area", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    # Broader than the existing "entry restricted for delivery", which missed
    # "Prohibited Area-Entry Restricted For" (57 this run).
    ("entry restricted", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("need department name", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("shifted from the given address", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("hal address", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("charges pending", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),

    # Blocked, needing someone to act -- NOT a failed attempt, so deliberately
    # ON_HOLD rather than inflating the NDR numbers.
    #
    # "Contact Customer Service" (99) is genuinely opaque: it states that
    # someone must intervene without saying why. ON_HOLD is the honest reading
    # and is non-terminal, so nothing is hidden by it.
    ("contact customer service", CanonicalStatus.ON_HOLD),
    # "Awb Rejected On Field" (94) is ambiguous between a rejected pickup and a
    # rejected delivery. Both readings are non-terminal and both need a human,
    # so ON_HOLD is safe either way -- claiming a failed DELIVERY would be a
    # guess about which end of the journey it happened at.
    ("awb rejected", CanonicalStatus.ON_HOLD),
    ("requested future delivery", CanonicalStatus.ON_HOLD),

    # Carrier-declared delays. Still moving: not terminal, not an NDR.
    ("delay caused beyond control", CanonicalStatus.IN_TRANSIT),
    ("delay expected", CanonicalStatus.IN_TRANSIT),
    ("network issues", CanonicalStatus.IN_TRANSIT),
    # "Delivery Scheduled For Next Working Day" (255) is the single biggest
    # unmapped string. It means the parcel is sitting at destination awaiting
    # tomorrow's round -- movement, not failure.
    ("delivery scheduled", CanonicalStatus.IN_TRANSIT),
    ("delivery on next business day", CanonicalStatus.IN_TRANSIT),

    # Movement.
    ("rcvd at destn loc", CanonicalStatus.REACHED_DESTINATION_HUB),
    ("shipment arrived", CanonicalStatus.IN_TRANSIT),
    ("further connected", CanonicalStatus.IN_TRANSIT),

    # Not yet picked up. "Ready To Despatch" (68) and "Out To P/U" (30) are
    # both pre-pickup states, so MANIFESTED -- treating them as movement would
    # start the transit clock early and understate pickup delays.
    ("ready to despatch", CanonicalStatus.MANIFESTED),
    ("out to p/u", CanonicalStatus.MANIFESTED),
    ("pickup has been registered", CanonicalStatus.MANIFESTED),

    ("in transit", CanonicalStatus.IN_TRANSIT),
    ("received at", CanonicalStatus.IN_TRANSIT),
    ("departed from", CanonicalStatus.IN_TRANSIT),
    ("shipment picked up", CanonicalStatus.PICKED_UP),
    ("picked up", CanonicalStatus.PICKED_UP),
    ("manifested", CanonicalStatus.MANIFESTED),
    ("cancelled", CanonicalStatus.CANCELLED),
    ("lost", CanonicalStatus.LOST_OR_DAMAGED),
)


def classify(phrase: str) -> tuple[CanonicalStatus, bool]:
    low = (phrase or "").strip().lower()
    if not low:
        return CanonicalStatus.UNKNOWN, False
    for needle, canonical in PHRASE_MAP:
        if needle in low:
            return canonical, True
    return CanonicalStatus.UNKNOWN, False


def parse(html: str, awb: str, ctx: FetchContext) -> TrackingResult:
    """Pure parser -- testable against reference/fixtures/bluedart_*.html."""
    tables = _TABLE.findall(html)
    summary: dict[str, str] = {}
    scans: list[list[str]] = []
    for t in tables:
        rows = [r for r in _rows(t) if any(c for c in r)]
        if not rows:
            continue
        if all(len(r) == 2 for r in rows) and any(
                r[0].lower().startswith("waybill no") for r in rows):
            summary = {r[0].strip().rstrip(":"): r[1].strip() for r in rows}
        elif any(len(r) == 4 for r in rows):
            body = [r for r in rows if len(r) == 4]
            # Drop the header row (Location, Details, Date, Time *).
            if body and body[0][0].strip().lower() == "location":
                body = body[1:]
            if body:
                scans = body

    if not summary.get("Waybill No"):
        # Structural absence -- the textual "Records Not Found" strings are a
        # hidden template present even on success.
        return TrackingResult(
            awb=awb, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
            outcome=FetchOutcome.NOT_FOUND,
            canonical_status=CanonicalStatus.AWB_NOT_FOUND)

    raw = summary.get("Status", "")
    canonical, mapped = classify(raw)
    if not mapped and raw and ctx.dq is not None:
        ctx.dq.unmapped_status(ADAPTER_ID, raw, awb, {"summary": summary})

    events: list[TrackEvent] = []
    attempts = 0
    ndr: str | None = None
    for location, details, date_s, time_s in scans:
        ev_canon, _ = classify(details)
        if ev_canon == CanonicalStatus.DELIVERY_ATTEMPT_FAILED:
            attempts += 1
            ndr = ndr or details
        events.append(TrackEvent(
            at=parse_date_time_pair(date_s, time_s), raw_status=details,
            canonical=ev_canon, location=location or None))

    status_at = None
    if canonical == CanonicalStatus.DELIVERED:
        status_at = parse_date_time_pair(summary.get("Date of Delivery"),
                                         summary.get("Time of Delivery"))
    if status_at is None and events:
        status_at = events[0].at

    return TrackingResult(
        awb=awb, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
        outcome=FetchOutcome.OK, canonical_status=canonical,
        raw_status=raw or None, raw_status_mapped=mapped, status_at=status_at,
        events=tuple(events), attempt_count=attempts or None,
        ndr_reason=(ndr if canonical == CanonicalStatus.DELIVERY_ATTEMPT_FAILED
                    else None),
        current_location=(events[0].location if events else None),
        destination=summary.get("To"),
        recipient=summary.get("Recipient") or None,
        raw_payload={"summary": summary, "scans": scans,
                     "pickup_date": summary.get("Pickup Date")},
    )


def track(awbs: Sequence[str], ctx: FetchContext) -> list[TrackingResult]:
    def fetch_one(awb: str) -> TrackingResult:
        r = ctx.http.request(
            ADAPTER_ID, "GET", API.format(awb=awb),
            min_interval_s=SPEC.min_interval_s,
            timeout=(SPEC.timeout_connect_s, SPEC.timeout_read_s),
            headers={"Accept": "text/html"},
            block_signatures=SPEC.block_signatures)
        res = parse(r.text or "", awb, ctx)
        res.http_status = r.status_code
        res.latency_ms = int(r.elapsed.total_seconds() * 1000)
        return res

    # run_loop applies SPEC.max_awbs_per_run now, so no manual capping here.
    return run_loop(SPEC, awbs, ctx, fetch_one)
