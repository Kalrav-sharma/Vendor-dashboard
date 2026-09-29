"""Shadowfax adapter -- 89% of in-scope volume, verified live 2026-09-07.

Two paths, chosen by whether SHADOWFAX_API_TOKEN is set:

OFFICIAL (Unified API, https://sfxunifiedapi.docs.apiary.io) -- used when the
token is set. Written against the published API Blueprint, not a live capture:
    POST https://dale.shadowfax.in/api/v4/clients/bulk_track/
    Authorization: Token <SHADOWFAX_API_TOKEN>
    {"awb_numbers": [...]}           -- max 50 per call (documented 400 above)
Response: {"message": "Success", "data": [{..., "awb_number", "status",
"status_display", "promised_delivery_date", "delivery_details": {...},
"tracking_details": [{"created", "location", "status_id", "status",
"remarks"}]}]}. Timestamps are UTC ("...Z") per the docs -- see _parse_utc().

PUBLIC (tokenless fallback, found in the tracker SPA's own bundle):
    GET https://saruman.shadowfax.in/web_app/delivery/track/{awb}/
    Authorization: Token <sfxTrackStaticToken>

That token is baked into Shadowfax's public JS bundle. It WILL rotate, so it is
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
from datetime import datetime, timezone
from typing import Any, Sequence

from ..dates import IST, parse_dt
from .base import (AdapterSpec, CanonicalStatus, FetchContext, FetchOutcome,
                   TrackEvent, TrackingResult)
from ._loop import run_loop
from .http import BlockedError, CircuitOpen

ADAPTER_ID = "shadowfax"

TRACKER_ORIGIN = "https://tracker.shadowfax.in"
API = "https://saruman.shadowfax.in/web_app/delivery/track/{awb}/"

OFFICIAL_BULK_URL = "https://dale.shadowfax.in/api/v4/clients/bulk_track/"
OFFICIAL_BATCH_SIZE = 50
OFFICIAL_MIN_INTERVAL_S = 0.5
#: ~100 calls/run at 50 AWBs each. The public path's cap of 60 existed only
#: because of the shared public quota; a client token has its own. A per-run
#: ctx.max_awbs_override still wins over this.
OFFICIAL_MAX_AWBS_PER_RUN = 5000

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
    """The PUBLIC tracker's token, re-derived from its bundle.

    SHADOWFAX_API_TOKEN is deliberately not read here: it is the Unified API
    client key, which track() routes to the official endpoint instead.
    """
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
    # --- Unified API status_ids (the docs' marketplace + warehouse "Order
    # States" tables). A different vocabulary from the public tracker above,
    # though the two overlap where the keys match (ofp, picked, ofd, ...).
    "new": CanonicalStatus.MANIFESTED,
    "assigned_for_seller_pickup": CanonicalStatus.MANIFESTED,
    "received_from_client_warehouse": CanonicalStatus.PICKED_UP,
    "recd_at_rev_hub": CanonicalStatus.PICKED_UP,
    "item_manifested": CanonicalStatus.IN_TRANSIT,
    "bag_in_transit": CanonicalStatus.IN_TRANSIT,
    "bag_received_at_via": CanonicalStatus.IN_TRANSIT,
    "recd_at_fwd_hub": CanonicalStatus.IN_TRANSIT,
    "item_misrouted": CanonicalStatus.IN_TRANSIT,
    "bag_received": CanonicalStatus.REACHED_DESTINATION_HUB,
    "recd_at_fwd_dc": CanonicalStatus.REACHED_DESTINATION_HUB,
    "assigned_for_delivery": CanonicalStatus.REACHED_DESTINATION_HUB,
    "cid": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "nc": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "na": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "reopen_ndr": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "seller_initiated_delay": CanonicalStatus.ON_HOLD,
    "seller_not_contactable": CanonicalStatus.ON_HOLD,
    "pickup_not_attempted": CanonicalStatus.ON_HOLD,
    "cancelled_by_seller": CanonicalStatus.CANCELLED,
    "cancelled_by_customer": CanonicalStatus.CANCELLED,
    "rts": CanonicalStatus.RTO_INITIATED,
    "rto": CanonicalStatus.RTO_INITIATED,
    "rts_in_process": CanonicalStatus.RTO_IN_TRANSIT,
    "rts_ofd": CanonicalStatus.RTO_IN_TRANSIT,
    "rts_nd": CanonicalStatus.RTO_IN_TRANSIT,
    "in_transit_return": CanonicalStatus.RTO_IN_TRANSIT,
    "rts_d": CanonicalStatus.RTO_DELIVERED,
    "rto_d": CanonicalStatus.RTO_DELIVERED,
    "lost": CanonicalStatus.LOST_OR_DAMAGED,
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


def _parse_utc(value: Any) -> datetime | None:
    """The Unified API's timestamps are UTC ("2024-08-27T11:29:31Z").

    parse_dt() strips a trailing Z and then ASSUMES IST, which would put every
    Shadowfax scan 5.5h in the past and manufacture STUCK alerts -- so parse
    naive here and stamp UTC explicitly.
    """
    dt = parse_dt(value, assume_ist=False)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


#: Customer name/phone/address and seller/tax lines are never needed to track a
#: shipment, and raw_payload lands in last_mile_alerts.raw -- so they are
#: dropped rather than copied into our tables.
_ADDRESS_KEEP = ("city", "state", "pincode")


def _redact(rec: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in rec.items()
           if k not in ("pickup_details", "delivery_details", "product_details")}
    for key in ("pickup_details", "delivery_details"):
        addr = rec.get(key)
        if isinstance(addr, dict):
            out[key] = {k: addr.get(k) for k in _ADDRESS_KEEP}
    return out


def parse_official(rec: dict[str, Any], awb: str,
                   ctx: FetchContext) -> TrackingResult:
    """Pure parser for one Unified API bulk_track `data` entry."""
    steps = [s for s in (rec.get("tracking_details") or []) if isinstance(s, dict)]
    # The docs list scans oldest-first; the rest of the pipeline reads
    # events[0] as the latest scan. ISO-UTC strings sort chronologically.
    steps.sort(key=lambda s: str(s.get("created") or ""), reverse=True)

    raw = str(rec.get("status") or rec.get("status_id")
              or (steps[0].get("status_id") if steps else "") or "").strip().lower()
    mapped = raw in STATUS_MAP
    canonical = STATUS_MAP.get(raw, CanonicalStatus.UNKNOWN)
    if not mapped and raw and ctx.dq is not None:
        ctx.dq.unmapped_status(ADAPTER_ID, raw, awb, _redact(rec))

    events: list[TrackEvent] = []
    for s in steps:
        sid = str(s.get("status_id") or "").strip().lower()
        events.append(TrackEvent(
            at=_parse_utc(s.get("created")),
            raw_status=str(s.get("status") or sid),
            canonical=STATUS_MAP.get(sid, CanonicalStatus.UNKNOWN),
            location=s.get("location") or None,
            remark=s.get("remarks") or None,
            raw_status_mapped=sid in STATUS_MAP,
        ))

    attempts = sum(1 for e in events
                   if e.canonical == CanonicalStatus.DELIVERY_ATTEMPT_FAILED)
    latest = events[0] if events else None
    return TrackingResult(
        awb=awb, adapter_id=ADAPTER_ID, courier_code="",
        fetched_at=ctx.now, outcome=FetchOutcome.OK,
        canonical_status=canonical, raw_status=raw or None,
        raw_status_mapped=mapped,
        status_at=latest.at if latest else None,
        events=tuple(events),
        expected_delivery=parse_dt(rec.get("promised_delivery_date")),
        attempt_count=attempts or None,
        ndr_reason=(latest.remark if latest
                    and canonical == CanonicalStatus.DELIVERY_ATTEMPT_FAILED else None),
        current_location=latest.location if latest else None,
        destination=(rec.get("delivery_details") or {}).get("city") or None,
        raw_payload=_redact(rec),
    )


def _track_official(awbs: Sequence[str], ctx: FetchContext,
                    token: str) -> list[TrackingResult]:
    """One POST per 50 AWBs against the Unified API's bulk_track."""
    cap = (ctx.max_awbs_override or {}).get(ADAPTER_ID, OFFICIAL_MAX_AWBS_PER_RUN)
    awbs = list(awbs)
    awbs, over = awbs[:cap], awbs[cap:]
    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}

    def fail(batch, outcome, err, http=None,
             status=CanonicalStatus.UNKNOWN) -> list[TrackingResult]:
        return [TrackingResult(
            awb=a, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
            outcome=outcome, canonical_status=status, http_status=http,
            error=str(err)[:200]) for a in batch]

    def fetch_batch(batch: list[str]) -> list[TrackingResult]:
        r = ctx.http.request(
            ADAPTER_ID, "POST", OFFICIAL_BULK_URL,
            json={"awb_numbers": batch},
            min_interval_s=OFFICIAL_MIN_INTERVAL_S,
            timeout=(SPEC.timeout_connect_s, SPEC.timeout_read_s),
            headers=headers, block_signatures=SPEC.block_signatures)
        if r.status_code in (401, 403):
            raise PermissionError(f"SHADOWFAX_API_TOKEN rejected (http={r.status_code})")
        if r.status_code == 400:
            # Documented for an invalid AWB, but not whether one bad AWB fails
            # the whole batch -- so split and retry until it is isolated,
            # rather than losing 49 good answers to one bad number.
            if len(batch) > 1:
                mid = len(batch) // 2
                return fetch_batch(batch[:mid]) + fetch_batch(batch[mid:])
            try:
                msg = str(r.json().get("message") or "")
            except Exception:
                msg = r.text[:120]
            return fail(batch, FetchOutcome.NOT_FOUND, msg or "http 400", 400,
                        CanonicalStatus.AWB_NOT_FOUND)
        if r.status_code == 429 or r.status_code >= 500:
            return fail(batch, FetchOutcome.TRANSIENT_ERROR,
                        f"http {r.status_code}", r.status_code)
        try:
            data = r.json().get("data")
        except Exception as exc:
            return fail(batch, FetchOutcome.PARSE_ERROR,
                        f"non-JSON response: {type(exc).__name__}", r.status_code)
        if not isinstance(data, list):
            return fail(batch, FetchOutcome.PARSE_ERROR,
                        "no `data` list in bulk_track response", r.status_code)

        found = {str(d.get("awb_number") or "").strip().upper(): d
                 for d in data if isinstance(d, dict)}
        out: list[TrackingResult] = []
        for a in batch:
            rec = found.get(a.strip().upper())
            if rec is None:
                out += fail([a], FetchOutcome.NOT_FOUND,
                            "not present in the bulk_track response",
                            r.status_code, CanonicalStatus.AWB_NOT_FOUND)
                continue
            res = parse_official(rec, a, ctx)
            res.http_status = r.status_code
            res.latency_ms = int(r.elapsed.total_seconds() * 1000)
            out.append(res)
        return out

    out: list[TrackingResult] = []
    stop: tuple[FetchOutcome, str] | None = None
    for i in range(0, len(awbs), OFFICIAL_BATCH_SIZE):
        batch = awbs[i:i + OFFICIAL_BATCH_SIZE]
        if stop is None and ctx.out_of_budget():
            stop = (FetchOutcome.SKIPPED_BUDGET, "run budget spent")
        if stop is not None:
            out += fail(batch, *stop)
            continue
        try:
            out += fetch_batch(batch)
        except PermissionError as exc:
            # Every remaining call would fail the same way.
            out += fail(batch, FetchOutcome.AUTH_REQUIRED, exc)
            stop = (FetchOutcome.AUTH_REQUIRED, f"skipped: {exc}")
        except BlockedError as exc:
            out += fail(batch, FetchOutcome.BLOCKED, exc)
            stop = (FetchOutcome.SKIPPED_BUDGET, "deferred: adapter blocked earlier this run")
        except CircuitOpen as exc:
            out += fail(batch, FetchOutcome.TRANSIENT_ERROR, exc)
            stop = (FetchOutcome.SKIPPED_BUDGET, "deferred: adapter aborted earlier this run")
        except Exception as exc:
            out += fail(batch, FetchOutcome.TRANSIENT_ERROR, f"{type(exc).__name__}: {exc}")
    out += fail(over, FetchOutcome.SKIPPED_BUDGET, f"deferred: over {ADAPTER_ID} cap={cap}")
    return out


def track(awbs: Sequence[str], ctx: FetchContext) -> list[TrackingResult]:
    """Official Unified API when SHADOWFAX_API_TOKEN is set, else the public path.

    The public path polls via the shared loop, so an abort records what went
    unpolled. This adapter used to carry its own inline loop, written before
    `_loop.py` existed. On abort it simply stopped, so the remaining AWBs were
    neither polled nor recorded -- on the first full-scale run that silently
    dropped 5,138 of 5,167 shipments, and the poll state had no idea.
    """
    official = ((ctx.secrets or {}).get("SHADOWFAX_API_TOKEN") or "").strip()
    if official:
        return _track_official(awbs, ctx, official)

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
