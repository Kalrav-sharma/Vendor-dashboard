"""Holisol adapter -- verified live 2026-09-07 (returned a populated payload).

    POST https://www.holisoldelivery.com/api/report/
         GetShipmentsTrack_ClientOnly?awbNo={awb}

No auth. Found in the AngularJS source behind holisollogistics.com/track-shipment.

Verified shape (AWB HL58916740440):
    {"TrackObj":[{"Message":"Delivered  ","CreatedAt":"7/13/2026 5:32:48 PM",
                  "City":"GURGAON","SerialNo":"6","NDRCode":null},
                 {"Message":"FE out for delivery  ", ...}]}

Two quirks: the array is newest-first (descending SerialNo), and `Message`
carries trailing whitespace that must be stripped before mapping.

An unknown AWB returns literal `null` -- not an error, not an empty array.

Caveat: this is an unauthenticated, internal-looking IIS endpoint with
`Access-Control-Allow-Origin: *`. It may change without notice. At 325 rows of
in-scope volume that risk is accepted rather than engineered around.
"""
from __future__ import annotations

from typing import Any, Sequence

from ..dates import parse_dt
from ._loop import run_loop
from .base import (AdapterSpec, CanonicalStatus, FetchContext, FetchOutcome,
                   TrackEvent, TrackingResult)

ADAPTER_ID = "holisol"
API = ("https://www.holisoldelivery.com/api/report/"
       "GetShipmentsTrack_ClientOnly?awbNo={awb}")

SPEC = AdapterSpec(
    adapter_id=ADAPTER_ID,
    display_name="Holisol",
    mode="public_json",
    enabled=True,
    max_concurrency=2,
    min_interval_s=0.5,
    awb_pattern=r"^HL\d{11}$",
    notes="Unauthenticated internal endpoint; low volume, fragility accepted.",
)

PHRASE_MAP: tuple[tuple[str, CanonicalStatus], ...] = (
    ("delivered", CanonicalStatus.DELIVERED),
    ("out for delivery", CanonicalStatus.OUT_FOR_DELIVERY),
    ("undelivered", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("not delivered", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("attempted", CanonicalStatus.DELIVERY_ATTEMPT_FAILED),
    ("rto", CanonicalStatus.RTO_IN_TRANSIT),
    ("return", CanonicalStatus.RTO_IN_TRANSIT),
    ("hold", CanonicalStatus.ON_HOLD),
    ("in transit", CanonicalStatus.IN_TRANSIT),
    ("received at", CanonicalStatus.IN_TRANSIT),
    ("reached", CanonicalStatus.REACHED_DESTINATION_HUB),
    ("picked", CanonicalStatus.PICKED_UP),
    ("pickup", CanonicalStatus.PICKED_UP),
    ("manifest", CanonicalStatus.MANIFESTED),
    ("booked", CanonicalStatus.MANIFESTED),
    ("cancel", CanonicalStatus.CANCELLED),
    ("lost", CanonicalStatus.LOST_OR_DAMAGED),
)


def classify(message: str) -> tuple[CanonicalStatus, bool]:
    low = (message or "").strip().lower()
    if not low:
        return CanonicalStatus.UNKNOWN, False
    for needle, canonical in PHRASE_MAP:
        if needle in low:
            return canonical, True
    return CanonicalStatus.UNKNOWN, False


def parse(payload: Any, awb: str, ctx: FetchContext) -> TrackingResult:
    """Pure parser -- `payload` may legitimately be None (unknown AWB)."""
    track_obj = (payload or {}).get("TrackObj") if isinstance(payload, dict) else None
    if not track_obj:
        return TrackingResult(
            awb=awb, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
            outcome=FetchOutcome.NOT_FOUND,
            canonical_status=CanonicalStatus.AWB_NOT_FOUND)

    def serial(entry: dict[str, Any]) -> int:
        try:
            return int(str(entry.get("SerialNo") or 0))
        except ValueError:
            return 0

    ordered = sorted(track_obj, key=serial, reverse=True)   # newest first
    events: list[TrackEvent] = []
    attempts = 0
    ndr: str | None = None
    for entry in ordered:
        msg = str(entry.get("Message") or "").strip()
        canon, mapped_ev = classify(msg)
        if canon == CanonicalStatus.DELIVERY_ATTEMPT_FAILED:
            attempts += 1
            ndr = ndr or (entry.get("NDRCode") or msg)
        events.append(TrackEvent(
            at=parse_dt(entry.get("CreatedAt")), raw_status=msg,
            canonical=canon, location=(entry.get("City") or None),
            remark=(entry.get("NDRCode") or None), raw_status_mapped=mapped_ev))

    latest = ordered[0]
    raw = str(latest.get("Message") or "").strip()
    canonical, mapped = classify(raw)
    if not mapped and raw and ctx.dq is not None:
        ctx.dq.unmapped_status(ADAPTER_ID, raw, awb, payload)

    return TrackingResult(
        awb=awb, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
        outcome=FetchOutcome.OK, canonical_status=canonical,
        raw_status=raw or None, raw_status_mapped=mapped,
        status_at=parse_dt(latest.get("CreatedAt")), events=tuple(events),
        attempt_count=attempts or None,
        ndr_reason=(ndr if canonical == CanonicalStatus.DELIVERY_ATTEMPT_FAILED
                    else None),
        current_location=(latest.get("City") or None),
        raw_payload=payload,
    )


def track(awbs: Sequence[str], ctx: FetchContext) -> list[TrackingResult]:
    def fetch_one(awb: str) -> TrackingResult:
        r = ctx.http.request(
            ADAPTER_ID, "POST", API.format(awb=awb),
            min_interval_s=SPEC.min_interval_s,
            timeout=(SPEC.timeout_connect_s, SPEC.timeout_read_s),
            headers={"Accept": "application/json"},
            block_signatures=SPEC.block_signatures)
        res = parse(r.json(), awb, ctx)
        res.http_status = r.status_code
        res.latency_ms = int(r.elapsed.total_seconds() * 1000)
        return res

    return run_loop(SPEC, awbs, ctx, fetch_one)
