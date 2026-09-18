"""Shadowfax adapter -- 89% of in-scope volume, verified live 2026-09-07.

Endpoint (found in the tracker SPA's own bundle):
    GET https://saruman.shadowfax.in/web_app/delivery/track/{awb}/
    Authorization: Token <sfxTrackStaticToken>

The token is baked into Shadowfax's public JS bundle. It WILL rotate, so it is
re-derived from the bundle at runtime and cached, rather than hardcoded. If the
extraction ever fails, the adapter reports AUTH_REQUIRED instead of guessing.

Verified response shape (AWB SF3653083849URM):
    {"message":"Success",
     "order_details":{"awb_number":..., "status_id":"delivered",
                      "final_status":"Order Delivered",
                      "exp_delivery_date":"13 July 2026, Monday",
                      "remarks":"on 13 July 2026, Monday at 09:43 AM"},
     "data":[{"main_status":"Delivered","sub_status":[...],
              "time":["2026-07-13T09:43:46"],"main_status_id":7}]}

Note the response masks the customer phone (75XXXXXXX0); we do not read it.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Sequence

from ..dates import parse_dt
from .base import (AdapterSpec, CanonicalStatus, FetchContext, FetchOutcome,
                   TrackEvent, TrackingResult)
from ._loop import run_loop
from .http import BlockedError, CircuitOpen

ADAPTER_ID = "shadowfax"

TRACKER_ORIGIN = "https://tracker.shadowfax.in"
API = "https://saruman.shadowfax.in/web_app/delivery/track/{awb}/"

SPEC = AdapterSpec(
    adapter_id=ADAPTER_ID,
    display_name="Shadowfax",
    mode="public_json",
    enabled=True,
    requires_credentials=False,
    batch_size=1,
    # MEASURED 2026-09-08, not guessed. At 4 concurrent / 0.15s the endpoint
    # returned 30x HTTP 429 against 27 OK. Sequential at 1.0s gave 15/15 OK;
    # a later 2.0s trial gave 9x 429, which is WORSE and rules out a
    # per-second rate limit -- it is a ROLLING QUOTA that the earlier trials
    # had already spent. So pace politely and cap the run.
    max_concurrency=1,
    min_interval_s=1.0,
    #: 60/run = ~1,440/day. Set low deliberately: even 1 concurrent request
    #: per second still drew 30x HTTP 429 once the quota was spent, so the
    #: limit is a budget over time, not a rate we can pace around. It is not enough
    #: to cover 5,167 open Shadowfax AWBs, and it does not need to be: Uniware
    #: already carries a carrier status for all but ~53 open shipments, so
    #: polling here is for freshness on the genuinely urgent few. Raise this
    #: only with a real Shadowfax Unified API token, not on the shared public
    #: one that every visitor to their tracking site uses.
    max_awbs_per_run=60,
    timeout_connect_s=10.0,
    timeout_read_s=30.0,
    awb_pattern=r"^SF\d{10}[A-Z]{3}$",
    notes="Shared public bundle token, rolling quota. Needs a real API token to scale.",
)

_token_lock = threading.Lock()
_token_cache: dict[str, Any] = {"token": None, "bundle": None}


def resolve_token(ctx: FetchContext) -> str | None:
    """An explicit SHADOWFAX_API_TOKEN wins; else re-derive from the bundle."""
    supplied = (ctx.secrets or {}).get("SHADOWFAX_API_TOKEN")
    if supplied:
        return supplied
    with _token_lock:
        if _token_cache["token"]:
            return _token_cache["token"]
        try:
            page = ctx.http.request(ADAPTER_ID, "GET", TRACKER_ORIGIN + "/",
                                    min_interval_s=SPEC.min_interval_s,
                                    timeout=(10.0, 30.0))
            refs = list(dict.fromkeys(
                re.findall(r'[\'"]([^\'"\s]+\.js)[\'"]', page.text)))
            same_origin = [b for b in refs if not b.startswith("http")]
            # The Angular main bundle holds the token; try it first.
            ordered = ([b for b in same_origin if "main" in b]
                       + [b for b in same_origin if "main" not in b])
            for b in ordered:
                url = TRACKER_ORIGIN + "/" + b.lstrip("/")
                js = ctx.http.request(ADAPTER_ID, "GET", url,
                                      min_interval_s=SPEC.min_interval_s,
                                      timeout=(10.0, 60.0))
                if js.status_code != 200:
                    continue
                m = re.search(r'sfxTrackStaticToken\s*:\s*"([A-Za-z0-9]+)"', js.text)
                if m:
                    _token_cache.update({"token": m.group(1), "bundle": url})
                    return m.group(1)
        except (BlockedError, CircuitOpen):
            raise
        except Exception:
            return None
    return None


#: status_id -> canonical. Grounded in the 23 values in Shadowfax's own
#: status_map, PLUS three its app compares against but never publishes
#: (`undelivered`, `on_hold`, `pickup_unsuccessfull` -- their misspelling,
#: preserved verbatim). Live proof that an LSP status list is never complete,
#: which is why an unmapped value must surface rather than default.
STATUS_MAP: dict[str, CanonicalStatus] = {
    "order_received": CanonicalStatus.MANIFESTED,
    "order_recd": CanonicalStatus.MANIFESTED,
    "ofp": CanonicalStatus.MANIFESTED,
    "picked": CanonicalStatus.PICKED_UP,
    "item_picked": CanonicalStatus.PICKED_UP,
    "in_transit": CanonicalStatus.IN_TRANSIT,
    "near_you": CanonicalStatus.IN_TRANSIT,
    "pincode_updated": CanonicalStatus.IN_TRANSIT,
    "ofd": CanonicalStatus.OUT_FOR_DELIVERY,
    "delivered": CanonicalStatus.DELIVERED,
    "undelivered": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "undelivered_na": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "undelivered_nc": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "undelivered_cid": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "ndr": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "delivery_failed": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "undelivered_on_hold": CanonicalStatus.ON_HOLD,
    "on_hold": CanonicalStatus.ON_HOLD,
    "pickup_on_hold": CanonicalStatus.ON_HOLD,
    "pickup_unsuccessfull": CanonicalStatus.ON_HOLD,
    "pickup_na": CanonicalStatus.ON_HOLD,
    "pickup_nc": CanonicalStatus.ON_HOLD,
    "pickup_cid": CanonicalStatus.ON_HOLD,
    "qc_failed": CanonicalStatus.ON_HOLD,
    "cancelled": CanonicalStatus.CANCELLED,
    "pickup_cancelled": CanonicalStatus.CANCELLED,
}


def parse(payload: dict[str, Any], awb: str, courier: str,
          ctx: FetchContext) -> TrackingResult:
    """Pure parser -- testable against a fixture, no network."""
    od = payload.get("order_details") or {}
    raw = (od.get("status_id") or "").strip().lower()
    mapped = raw in STATUS_MAP
    canonical = STATUS_MAP.get(raw, CanonicalStatus.UNKNOWN)
    if not mapped and raw and ctx.dq is not None:
        ctx.dq.unmapped_status(ADAPTER_ID, raw, awb, payload)

    events: list[TrackEvent] = []
    for step in payload.get("data") or []:
        times = step.get("time") or []
        subs = step.get("sub_status") or []
        at = parse_dt(times[0]) if times else None
        events.append(TrackEvent(
            at=at,
            raw_status=str(step.get("main_status") or ""),
            canonical=canonical if step is (payload.get("data") or [None])[0]
            else CanonicalStatus.IN_TRANSIT,
            remark="; ".join(str(s) for s in subs) or None,
            raw_status_mapped=mapped,
        ))

    status_at = events[0].at if events else None
    # Shadowfax puts the delivery moment in `remarks` when `time` is absent.
    if status_at is None:
        status_at = parse_dt(od.get("exp_delivery_date"))

    return TrackingResult(
        awb=awb, adapter_id=ADAPTER_ID, courier_code=courier,
        fetched_at=ctx.now, outcome=FetchOutcome.OK,
        canonical_status=canonical, raw_status=raw or None,
        raw_status_mapped=mapped, status_at=status_at,
        events=tuple(events),
        expected_delivery=parse_dt(od.get("exp_delivery_date")),
        ndr_reason=(od.get("remarks") or None
                    if canonical == CanonicalStatus.DELIVERY_ATTEMPT_FAILED else None),
        raw_payload=payload,
    )


def track(awbs: Sequence[str], ctx: FetchContext) -> list[TrackingResult]:
    """Poll via the shared loop, so an abort records what went unpolled.

    This adapter used to carry its own inline loop, written before `_loop.py`
    existed. On abort it simply stopped, so the remaining AWBs were neither
    polled nor recorded -- on the first full-scale run that silently dropped
    5,138 of 5,167 shipments, and the poll state had no idea they were missed.
    """
    token = resolve_token(ctx)
    if not token:
        return [TrackingResult(
            awb=a, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
            outcome=FetchOutcome.AUTH_REQUIRED,
            canonical_status=CanonicalStatus.UNKNOWN,
            error="could not resolve sfxTrackStaticToken from the public bundle",
        ) for a in awbs]

    def fetch_one(awb: str) -> TrackingResult:
        r = ctx.http.request(
            ADAPTER_ID, "GET", API.format(awb=awb),
            min_interval_s=SPEC.min_interval_s,
            timeout=(SPEC.timeout_connect_s, SPEC.timeout_read_s),
            headers={"Authorization": f"Token {token}",
                     "Accept": "application/json"},
            block_signatures=SPEC.block_signatures)

        if r.status_code in (401, 403):
            # The token rotated or was rejected. Drop the cache so the next run
            # re-derives it, and abort this adapter -- every remaining call
            # would fail the same way. CircuitOpen is what run_loop treats as
            # an abort, and it records the rest as deferred.
            with _token_lock:
                _token_cache["token"] = None
            raise CircuitOpen(
                f"{ADAPTER_ID}: token rejected (http={r.status_code}); "
                "will re-derive next run")

        payload = r.json()
        if not (payload.get("order_details") or {}).get("awb_number"):
            return TrackingResult(
                awb=awb, adapter_id=ADAPTER_ID, courier_code="",
                fetched_at=ctx.now, outcome=FetchOutcome.NOT_FOUND,
                canonical_status=CanonicalStatus.AWB_NOT_FOUND,
                http_status=r.status_code,
                raw_status=str(payload.get("message") or "")[:120])

        res = parse(payload, awb, "", ctx)
        res.http_status = r.status_code
        res.latency_ms = int(r.elapsed.total_seconds() * 1000)
        return res

    return run_loop(SPEC, awbs, ctx, fetch_one)
