"""Delhivery adapter -- verified live 2026-09-07.

    GET https://dlv-api.delhivery.com/v3/unified-tracking?wbn={awb}
    Origin: https://www.delhivery.com      <- required; no token needed

Auth is an Origin allowlist, not a token: without the header the API answers
`{"statusCode":401,"message":"ERROR: Invalid Origin"}`.

Verified shape (AWB 29545110167484):
    data[0].status = {status:"DELIVERED", statusDateTime:"2026-08-14T07:50:29.002000",
                      statusType:"DL", instructions:"Delivered to consignee ..."}
    data[0].promiseDeliveryDate = "2026-08-17T23:59:59"   <- the LSP's own promise
    data[0].trackingStates[].scans[] = {scan, scanNslRemark, cityLocation,
                                        scannedLocation, scanType, scanDateTime}
    data[0].currentFlow = "Forward" | "Reverse"

IMPORTANT RETENTION LIMIT, found live: Delhivery hides tracking once a shipment
has been in an end state for 7+ days --
  "tracking details are not visible if the shipment reached end state 7 or more
   days ago"
so a delivery we never polled inside that window is unrecoverable from here.
That is an argument for polling promptly, and for trusting Uniware's
`Shipping Tracking Status` for older shipments.
"""
from __future__ import annotations

from typing import Any, Sequence

from ..dates import parse_dt
from ._loop import run_loop
from .base import (AdapterSpec, CanonicalStatus, FetchContext, FetchOutcome,
                   TrackEvent, TrackingResult)

ADAPTER_ID = "delhivery"
API = "https://dlv-api.delhivery.com/v3/unified-tracking?wbn={awb}"
ORIGIN = "https://www.delhivery.com"

#: The OFFICIAL B2C tracking API. Used only when DELHIVERY_API_TOKEN is set.
#: Documented 2026-09-14: up to 50 comma-separated waybills per request,
#: ~130ms average latency, so a full run collapses from one call per AWB to a
#: handful. DELHIVERY_API_BASE can point this at the staging host
#: (https://staging-express.delhivery.com) to rehearse a new token safely.
API_OFFICIAL_BASE = "https://track.delhivery.com"
API_OFFICIAL_PATH = "/api/v1/packages/json/"
#: Documented maximum. Going above it is the caller's bug, not Delhivery's.
OFFICIAL_BATCH_SIZE = 50

SPEC = AdapterSpec(
    adapter_id=ADAPTER_ID,
    display_name="Delhivery",
    mode="public_json",
    enabled=True,
    max_concurrency=3,
    min_interval_s=0.3,
    awb_pattern=r"^\d{14}$",
    notes="Origin-allowlisted, tokenless. 7-day retention after end state.",
)

#: `status.status` -> canonical. The `DL`/`UD`/`RT` statusType prefix and
#: `currentFlow` disambiguate forward vs reverse leg.
STATUS_MAP: dict[str, CanonicalStatus] = {
    "DELIVERED": CanonicalStatus.DELIVERED,
    "MANIFESTED": CanonicalStatus.MANIFESTED,
    "NOT PICKED": CanonicalStatus.MANIFESTED,
    "PENDING": CanonicalStatus.IN_TRANSIT,
    "IN TRANSIT": CanonicalStatus.IN_TRANSIT,
    "DISPATCHED": CanonicalStatus.OUT_FOR_DELIVERY,
    "OUT FOR DELIVERY": CanonicalStatus.OUT_FOR_DELIVERY,
    "UNDELIVERED": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "RTO": CanonicalStatus.RTO_IN_TRANSIT,
    "RTO INITIATED": CanonicalStatus.RTO_INITIATED,
    "RTO IN TRANSIT": CanonicalStatus.RTO_IN_TRANSIT,
    "RTO DELIVERED": CanonicalStatus.RTO_DELIVERED,
    "LOST": CanonicalStatus.LOST_OR_DAMAGED,
    "DAMAGED": CanonicalStatus.LOST_OR_DAMAGED,
    "CANCELED": CanonicalStatus.CANCELLED,
    "CANCELLED": CanonicalStatus.CANCELLED,
    "ON HOLD": CanonicalStatus.ON_HOLD,
    # Observed live 2026-09-08 from real polls.
    "OUT_DELIVERY": CanonicalStatus.OUT_FOR_DELIVERY,
    "REACHED_DEST_CITY": CanonicalStatus.REACHED_DESTINATION_HUB,
    # "SEL" is the seller: reaching the seller's city is the return leg.
    "REACHED_SEL_CITY": CanonicalStatus.RTO_IN_TRANSIT,
    "DELIVERED_SELLER": CanonicalStatus.RTO_DELIVERED,
    # RETURNED is ambiguous between "on its way back" and "back with us".
    # Mapped to the NON-terminal reading on purpose: RTO_DELIVERED is terminal
    # and would drop the shipment off the board, so guessing that direction
    # hides a live case, while guessing this direction merely keeps it visible
    # one cycle longer. `currentFlow` upgrades it when the carrier is explicit.
    "RETURNED": CanonicalStatus.RTO_IN_TRANSIT,
    # Observed live 2026-09-14 across all 160 due shipments. The API returns
    # UNDERSCORE forms; the spaced spellings above came from documentation and
    # never appear in practice, which is why IN_TRANSIT sat unmapped (64 of 160
    # shipments) while "IN TRANSIT" was mapped all along.
    "IN_TRANSIT": CanonicalStatus.IN_TRANSIT,
    # The _SELLER suffix is the return leg, consistent with DELIVERED_SELLER
    # (terminal) and REACHED_SEL_CITY (in transit). "Out for delivery to the
    # seller" means it is on the van going BACK -- not yet with the seller --
    # so the non-terminal RTO_IN_TRANSIT is the correct and safe reading.
    "OUT_DELIVERY_SELLER": CanonicalStatus.RTO_IN_TRANSIT,
    # Seen in the DQ sink on 2026-09-08, absent from today's live sample, so
    # these two are mapped from Delhivery's own flow rather than observation:
    # MANIFESTED -> WAITING_PICKUP -> PICKUP -> IN_TRANSIT -> OUT_DELIVERY.
    # "NOT PICKED" already maps to MANIFESTED, which fixes WAITING_PICKUP's
    # meaning; PICKUP is the scan AFTER collection, so it is movement.
    # Both are non-terminal, so an error here costs visibility, not truth.
    "WAITING_PICKUP": CanonicalStatus.MANIFESTED,
    "PICKUP": CanonicalStatus.IN_TRANSIT,
}

#: Phrases that mean "no data", not "no shipment".
_ABSENT = ("invalid awb", "very old package", "not visible")


def parse(payload: dict[str, Any], awb: str, ctx: FetchContext) -> TrackingResult:
    """Pure parser -- testable against reference/fixtures/delhivery_*.json."""
    data = (payload.get("data") or [])
    if not data:
        msg = str(payload.get("message") or "")
        low = msg.lower()
        return TrackingResult(
            awb=awb, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
            outcome=FetchOutcome.NOT_FOUND,
            canonical_status=CanonicalStatus.AWB_NOT_FOUND,
            raw_status=msg[:160],
            error=("retention window elapsed" if "visible" in low
                   else None if any(a in low for a in _ABSENT) else msg[:160]))

    d = data[0]
    st = d.get("status") or {}
    raw = (st.get("status") or "").strip().upper()
    reverse = (d.get("currentFlow") or "").strip().lower() == "reverse"

    mapped = raw in STATUS_MAP
    canonical = STATUS_MAP.get(raw, CanonicalStatus.UNKNOWN)
    if not mapped and raw and ctx.dq is not None:
        ctx.dq.unmapped_status(ADAPTER_ID, raw, awb, payload)
    # A delivery on the reverse leg is an RTO completion, not a customer delivery.
    if reverse and canonical == CanonicalStatus.DELIVERED:
        canonical = CanonicalStatus.RTO_DELIVERED

    events: list[TrackEvent] = []
    attempts = 0
    ndr: str | None = None
    for state in d.get("trackingStates") or []:
        for scan in state.get("scans") or []:
            stype = (scan.get("scanType") or "").upper()
            scan_raw = str(scan.get("scan") or state.get("label") or "")
            if stype == "UD":
                attempts += 1
                ndr = scan.get("scanNslRemark") or ndr
            events.append(TrackEvent(
                at=parse_dt(scan.get("scanDateTime")) or parse_dt(state.get("scanDateTime")),
                raw_status=scan_raw,
                canonical=STATUS_MAP.get(scan_raw.strip().upper(),
                                         CanonicalStatus.IN_TRANSIT),
                location=scan.get("scannedLocation") or scan.get("cityLocation"),
                remark=scan.get("scanNslRemark"),
            ))

    return TrackingResult(
        awb=awb, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
        outcome=FetchOutcome.OK, canonical_status=canonical,
        raw_status=raw or None, raw_status_mapped=mapped,
        status_at=parse_dt(st.get("statusDateTime")),
        events=tuple(events),
        expected_delivery=parse_dt(d.get("promiseDeliveryDate")),
        attempt_count=attempts or None,
        ndr_reason=(ndr or st.get("instructions")
                    if canonical == CanonicalStatus.DELIVERY_ATTEMPT_FAILED else None),
        current_location=(events[0].location if events else None),
        destination=d.get("destination"),
        raw_payload=payload,
    )


def _official_status_and_scans(shp: dict[str, Any]) -> tuple[dict, list]:
    """Pull Status and Scans out of one Shipment object.

    Kept separate so the shape assumption lives in ONE place: if Delhivery's
    payload differs from the documentation, this is the only thing to correct.
    """
    status = shp.get("Status") or {}
    scans = []
    for row in shp.get("Scans") or []:
        detail = row.get("ScanDetail") if isinstance(row, dict) else None
        scans.append(detail or row or {})
    return status, scans


def parse_official(shp: dict[str, Any], awb: str, ctx: FetchContext) -> TrackingResult:
    """Parse one Shipment from the official API.

    Status strings here are title case ("Delivered", "In Transit"), which
    uppercase onto the SPACED keys that have sat in STATUS_MAP since the
    documentation was first read -- the underscore keys belong to the tokenless
    endpoint. Both vocabularies therefore coexist on purpose.
    """
    status, scans = _official_status_and_scans(shp)
    raw = str(status.get("Status") or "").strip().upper()
    stype = str(status.get("StatusType") or "").strip().upper()

    mapped = raw in STATUS_MAP
    canonical = STATUS_MAP.get(raw, CanonicalStatus.UNKNOWN)
    if not mapped and raw and ctx.dq is not None:
        ctx.dq.unmapped_status(ADAPTER_ID, raw, awb, shp)
    # StatusType RT is the reverse leg; a "delivery" there is an RTO completion.
    if stype == "RT" and canonical == CanonicalStatus.DELIVERED:
        canonical = CanonicalStatus.RTO_DELIVERED

    events: list[TrackEvent] = []
    attempts = 0
    ndr: str | None = None
    for sc in scans:
        sc_type = str(sc.get("ScanType") or "").strip().upper()
        sc_raw = str(sc.get("Scan") or "").strip()
        if sc_type == "UD":
            attempts += 1
            ndr = sc.get("Instructions") or ndr
        events.append(TrackEvent(
            at=parse_dt(sc.get("ScanDateTime")),
            raw_status=sc_raw,
            canonical=STATUS_MAP.get(sc_raw.upper(), CanonicalStatus.IN_TRANSIT),
            location=sc.get("ScannedLocation"),
            remark=sc.get("Instructions"),
        ))

    return TrackingResult(
        awb=awb, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
        outcome=FetchOutcome.OK, canonical_status=canonical,
        raw_status=raw or None, raw_status_mapped=mapped,
        status_at=parse_dt(status.get("StatusDateTime")),
        events=tuple(events),
        expected_delivery=parse_dt(shp.get("PromisedDeliveryDate")),
        attempt_count=attempts or None,
        ndr_reason=(ndr or status.get("Instructions")
                    if canonical == CanonicalStatus.DELIVERY_ATTEMPT_FAILED else None),
        current_location=(events[0].location if events else None),
        destination=shp.get("Destination"),
        raw_payload=shp,
    )


def _track_official(awbs: Sequence[str], ctx: FetchContext,
                    token: str) -> list[TrackingResult]:
    """One request per 50 AWBs against the official API."""
    base = (ctx.secrets or {}).get("DELHIVERY_API_BASE") or API_OFFICIAL_BASE
    url = base.rstrip("/") + API_OFFICIAL_PATH
    out: list[TrackingResult] = []

    def fail(batch, outcome, err, http=None):
        return [TrackingResult(
            awb=a, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
            outcome=outcome, canonical_status=CanonicalStatus.UNKNOWN,
            http_status=http, error=err[:200]) for a in batch]

    for i in range(0, len(awbs), OFFICIAL_BATCH_SIZE):
        batch = list(awbs[i:i + OFFICIAL_BATCH_SIZE])
        if ctx.out_of_budget():
            out += fail(batch, FetchOutcome.SKIPPED_BUDGET, "run budget spent")
            continue
        r = ctx.http.request(
            ADAPTER_ID, "GET", url,
            params={"waybill": ",".join(batch), "ref_ids": ""},
            min_interval_s=SPEC.min_interval_s,
            timeout=(SPEC.timeout_connect_s, SPEC.timeout_read_s),
            headers={"Authorization": "Token " + token,
                     "Content-Type": "application/json",
                     "Accept": "application/json"},
            block_signatures=SPEC.block_signatures)
        if r.status_code in (401, 403):
            out += fail(batch, FetchOutcome.AUTH_REQUIRED,
                        "DELHIVERY_API_TOKEN rejected", r.status_code)
            continue
        if r.status_code >= 500 or r.status_code == 429:
            out += fail(batch, FetchOutcome.TRANSIENT_ERROR,
                        f"http {r.status_code}", r.status_code)
            continue
        try:
            payload = r.json()
        except Exception as exc:
            out += fail(batch, FetchOutcome.PARSE_ERROR,
                        f"non-JSON response: {type(exc).__name__}", r.status_code)
            continue

        # The shape is from DOCUMENTATION, never a live capture. If it is not
        # what we expect, say so loudly instead of inventing statuses.
        shipments = payload.get("ShipmentData")
        if not isinstance(shipments, list):
            out += fail(batch, FetchOutcome.PARSE_ERROR,
                        "no ShipmentData list; top-level keys="
                        + ",".join(sorted(payload)[:8]), r.status_code)
            continue

        found: dict[str, dict] = {}
        for entry in shipments:
            shp = (entry or {}).get("Shipment") if isinstance(entry, dict) else None
            if not isinstance(shp, dict):
                continue
            key = str(shp.get("AWB") or shp.get("Waybill") or "").strip()
            if key:
                found[key] = shp

        for a in batch:
            shp = found.get(str(a))
            if shp is None:
                out.append(TrackingResult(
                    awb=a, adapter_id=ADAPTER_ID, courier_code="",
                    fetched_at=ctx.now, outcome=FetchOutcome.NOT_FOUND,
                    canonical_status=CanonicalStatus.AWB_NOT_FOUND,
                    http_status=r.status_code,
                    error="not present in the batch response"))
                continue
            res = parse_official(shp, a, ctx)
            res.http_status = r.status_code
            out.append(res)
    return out


def track(awbs: Sequence[str], ctx: FetchContext) -> list[TrackingResult]:
    # Official batched API when a token exists; otherwise the tokenless path
    # below, which is what has actually been running and verified.
    token = ((ctx.secrets or {}).get("DELHIVERY_API_TOKEN") or "").strip()
    if token:
        return _track_official(awbs, ctx, token)

    def fetch_one(awb: str) -> TrackingResult:
        r = ctx.http.request(
            ADAPTER_ID, "GET", API.format(awb=awb),
            min_interval_s=SPEC.min_interval_s,
            timeout=(SPEC.timeout_connect_s, SPEC.timeout_read_s),
            headers={"Origin": ORIGIN, "Referer": ORIGIN + "/",
                     "Accept": "application/json"},
            block_signatures=SPEC.block_signatures)
        if r.status_code == 401:
            return TrackingResult(
                awb=awb, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
                outcome=FetchOutcome.AUTH_REQUIRED,
                canonical_status=CanonicalStatus.UNKNOWN, http_status=401,
                error="Origin rejected by Delhivery")
        payload = r.json()
        res = parse(payload, awb, ctx)
        res.http_status = r.status_code
        res.latency_ms = int(r.elapsed.total_seconds() * 1000)
        return res

    return run_loop(SPEC, awbs, ctx, fetch_one)
