"""DTDC adapter -- REST Tracking API v4, verified live 2026-09-10.

    POST https://blktracksvc.dtdc.com/dtdc-api/rest/JSONCnTrk/getTrackDetails
    headers: Content-Type: application/json, x-access-token: <token>
    body:    {"trkType":"cnno","strcnno":"<awb>","addtnlDtl":"Y"}

This REPLACES the earlier Shipsy guess (`dtdcapi.shipsy.io` + `DTDC_API_KEY`),
which was never confirmed and is now dead. Scraping stays a hard "no" -- see
the history at the bottom of this docstring.

CREDENTIAL SHAPE, settled by the working command line UC was given: the token
is a STATIC customer key of the form `GL#####_trk_json:<32 hex>`, not a session
token minted from a login pair. So `DTDC_ACCESS_TOKEN` is sent verbatim and
there is no authenticate call. The vendor doc's username/password -> token flow
is kept as a fallback (DTDC_USERNAME / DTDC_PASSWORD) because DTDC documents
both and another UC account may be issued the other kind; token wins if set.

Doc traps, all three confirmed against the live endpoint 2026-09-10:
  * The doc's "Production URL" for tracking is
    `http://dtdcstagingapi.dtdc.com/dtdc-tracking-api/dtdc-api/rest/...`
    -- staging host, plain HTTP, and an extra `/dtdc-tracking-api` segment.
    All three are wrong. The real path is `/dtdc-api/rest/JSONCnTrk/...` on
    `https://blktracksvc.dtdc.com`.
  * An unknown consignment answers **HTTP 206**, not 404, with
    `statusFlag:false` and `strError:"NO DATA FOUND FOR THIS CNNO NUMBER"`.
    Treating non-200 as failure would mark every absent AWB transient.
  * The response spells one field `sTrRemarks` -- lowercase s, capital T.
    That is NOT `strRemarks`, which is a different, header-level field.

Measured 2026-09-10 on real UC consignments: 6/6 success, p50 ~500 ms, ~3 KB
per response. Rate limit probed at 8 and 16 concurrent (24 and 64 requests):
zero 429s, ~14 req/s sustained. Unlike Shadowfax there is no sign of a rolling
quota -- but that is absence of evidence at small scale, so this adapter still
declares a conservative concurrency and the shared limiter and circuit breaker
apply exactly as for every other carrier.

Why this matters beyond DTDC's own volume: only 29 of UC's 2,907 DTDC
shipments need a live poll (Uniware already carries a terminal status for the
rest), so the hourly cost is ~20 seconds. What the API adds that Uniware
cannot is `strExpectedDeliveryDate` -- the CARRIER'S OWN promise -- plus coded
NDR reasons, which allow grading DTDC against what it committed to rather than
only against our SLA rules.

HISTORY -- do not re-litigate: the public tracking page
(www.dtdc.com/track-your-shipment) is captcha-gated and actively bot-blocks,
reproduced 2026-09-07 (a fake `HTTP 500` carrying `Blocked!` after ~5
requests). This process shares egress with the Uniware pull, so a ban would
break the daily pull too. Never enable this adapter by scraping.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from ..dates import IST
from ._loop import run_loop
from .base import (AdapterSpec, CanonicalStatus, FetchContext, FetchOutcome,
                   TrackEvent, TrackingResult)

ADAPTER_ID = "dtdc"
API = "https://blktracksvc.dtdc.com/dtdc-api/rest/JSONCnTrk/getTrackDetails"
AUTH_API = ("https://blktracksvc.dtdc.com/dtdc-api/api/dtdc/authenticate"
            "?username={u}&password={p}")

SPEC = AdapterSpec(
    adapter_id=ADAPTER_ID,
    display_name="DTDC",
    mode="official_api",
    enabled=True,
    requires_credentials=True,
    max_concurrency=4,
    min_interval_s=0.15,
    # Real UC consignments look like 7V105153623 / 7D111521048.
    awb_pattern=r"^\d[A-Z]\d{9}$",
    disabled_reason=None,
    notes="REST v4, static x-access-token. NEVER enable via scraping.",
)

#: `trackDetails[].strCode` -> canonical. Sourced from DTDC's own "DOMESTIC
#: TRACK EVENTS" sheet (71 codes), not inferred from prose. Matched EXACTLY on
#: the code, never as a substring: `RTODLV` and `DLV` differ by a prefix and
#: mean opposite things.
STATUS_MAP: dict[str, CanonicalStatus] = {
    # -- pickup / booking ---------------------------------------------------
    "PCAW": CanonicalStatus.MANIFESTED,      # Pickup Awaited
    "PCSC": CanonicalStatus.MANIFESTED,      # Pickup Scheduled
    "PCRA": CanonicalStatus.MANIFESTED,      # Pickup Reassigned
    "PCNO": CanonicalStatus.MANIFESTED,      # Not Picked -- still with shipper
    "PCUP": CanonicalStatus.PICKED_UP,
    "PCAN": CanonicalStatus.CANCELLED,       # Archived
    "BKD": CanonicalStatus.MANIFESTED,
    "SPL": CanonicalStatus.MANIFESTED,       # Softdata Upload (pre-booking)
    "DRAW": CanonicalStatus.MANIFESTED,      # Dropoff Awaited
    "DRSC": CanonicalStatus.MANIFESTED,      # Dropoff Scheduled
    "DRCOM": CanonicalStatus.PICKED_UP,      # Dropoff Completed
    "DRCAN": CanonicalStatus.CANCELLED,      # Dropoff Cancelled
    "DRREC": CanonicalStatus.CANCELLED,      # Dropoff Rejected
    # -- line haul ----------------------------------------------------------
    # The I*/O* pairs are inbound/outbound manifest scans at each hop. DTDC's
    # own sheet labels all thirteen simply "In Transit"; we do not invent a
    # finer meaning than the carrier publishes.
    "IPMF": CanonicalStatus.IN_TRANSIT,
    "OPMF": CanonicalStatus.IN_TRANSIT,
    "ORMF": CanonicalStatus.IN_TRANSIT,
    "IBMD": CanonicalStatus.IN_TRANSIT,
    "OBMD": CanonicalStatus.IN_TRANSIT,
    "IBMN": CanonicalStatus.IN_TRANSIT,
    "OBMN": CanonicalStatus.IN_TRANSIT,
    "IMBM": CanonicalStatus.IN_TRANSIT,
    "OMBM": CanonicalStatus.IN_TRANSIT,
    "IRBO": CanonicalStatus.IN_TRANSIT,
    "ORBO": CanonicalStatus.IN_TRANSIT,
    "CDIN": CanonicalStatus.IN_TRANSIT,
    "CDOUT": CanonicalStatus.IN_TRANSIT,
    "IRMF": CanonicalStatus.IN_TRANSIT,      # Mis Route
    "ARAP": CanonicalStatus.IN_TRANSIT,      # Arrived At Airport
    "LNRC": CanonicalStatus.IN_TRANSIT,      # Not Received at the next hop
    "CSCL": CanonicalStatus.IN_TRANSIT,      # Customs Cleared
    "RELHLD": CanonicalStatus.IN_TRANSIT,    # Released From Facility
    "REVOKESTOPDLV": CanonicalStatus.IN_TRANSIT,
    "INSCAN": CanonicalStatus.REACHED_DESTINATION_HUB,
    "RADCDIN": CanonicalStatus.REACHED_DESTINATION_HUB,
    "PREPERD": CanonicalStatus.REACHED_DESTINATION_HUB,   # DRS Prepared
    # -- held ---------------------------------------------------------------
    "HLDUP": CanonicalStatus.ON_HOLD,
    "HELDUP": CanonicalStatus.ON_HOLD,       # Held Up At Facility
    "CHLD": CanonicalStatus.ON_HOLD,         # Customs HeldUp
    "STOPDLV": CanonicalStatus.ON_HOLD,
    # "Shipment under investigation" is where a lost parcel begins, but DTDC
    # says investigation, not loss. ON_HOLD is non-terminal so the shipment
    # stays on the board; LOST_OR_DAMAGED is terminal and would drop it.
    "SUI": CanonicalStatus.ON_HOLD,
    # -- last mile ----------------------------------------------------------
    "OUTDLV": CanonicalStatus.OUT_FOR_DELIVERY,
    "NONDLV": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "DLV": CanonicalStatus.DELIVERED,
    # -- RTO ----------------------------------------------------------------
    # Every RTO* code is terminal for this board (see base.RETURN_TERMINAL): a
    # shipment that has turned around will not reach the customer. They are
    # still counted per carrier as rto_in_flight / rto_completed.
    "SETRTO": CanonicalStatus.RTO_INITIATED,
    "RTOW": CanonicalStatus.RTO_INITIATED,   # Waiting For RTO Approval
    "IRTO": CanonicalStatus.RTO_INITIATED,   # RTO Received
    "RTOBKD": CanonicalStatus.RTO_INITIATED,
    "RTO": CanonicalStatus.RTO_IN_TRANSIT,   # RTO Processed & Forwarded
    "RTOOPMF": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOIPMF": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOIRMF": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOORMF": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOOBMD": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOIBMD": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOOBMN": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOIBMN": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOOMBM": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOIMBM": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOORBO": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOIRBO": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOCDOUT": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOCDIN": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOINSCAN": CanonicalStatus.RTO_IN_TRANSIT,
    "RTORADCDIN": CanonicalStatus.RTO_IN_TRANSIT,
    "RTOOUTDLV": CanonicalStatus.RTO_IN_TRANSIT,
    "RTONONDLV": CanonicalStatus.RTO_IN_TRANSIT,
    "RETURND": CanonicalStatus.RTO_IN_TRANSIT,
    "RTORETURND": CanonicalStatus.RTO_IN_TRANSIT,
    "RTODLV": CanonicalStatus.RTO_DELIVERED,
}

#: `trackHeader.strStatus` -> canonical. A COARSER vocabulary than the scan
#: codes (the doc lists five; the live API answers in title case), used only
#: when `addtnlDtl:"N"` or an empty trackDetails leaves no scan to read.
HEADER_STATUS_MAP: dict[str, CanonicalStatus] = {
    "DELIVERED": CanonicalStatus.DELIVERED,
    "DELIVERY PROCESS IN PROGRESS": CanonicalStatus.OUT_FOR_DELIVERY,
    "ATTEMPTED": CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
    "HELDUP": CanonicalStatus.ON_HOLD,
    "HELD UP": CanonicalStatus.ON_HOLD,
    "RTO": CanonicalStatus.RTO_IN_TRANSIT,
    "IN TRANSIT": CanonicalStatus.IN_TRANSIT,
    "BOOKED": CanonicalStatus.MANIFESTED,
    "PENDING": CanonicalStatus.IN_TRANSIT,
}

#: NDR reason code -> description, from the sheet's "NON DELIVERY REASON"
#: block. `sTrRemarks` on a NONDLV scan is `CODE|DESCRIPTION`; DTDC's own text
#: is kept verbatim and this map is used only to recognise the code. The
#: suffix assigns fault -- (CIR) consignee, (DIR) DTDC, (OTR) other -- which is
#: what decides whether an NDR is chargeable to the carrier.
NDR_REASONS: dict[str, str] = {
    "ADR": "ADDRESS INCOMPLETE OR WRONG-(CIR)",
    "CAD": "RECEIVER REQUESTED DELIVERY ON ANOTHER DATE-(CIR)",
    "CAN": "COLLECTION AMOUNT NOT READY-(CIR)",
    "COC": "COVID 19 - CUSTOMER REFUSED TO ACCEPT",
    "CWP": "ADDRESS CORRECT AND PINCODE WRONG-(CIR)",
    "DLK": "OFFICE CLOSED OR DOOR LOCK-(CIR)",
    "DNM": "CONTACT NAME / DEPT NOT MENTIONED-(CIR)",
    "DTD": "RECEIVER REFUSE DELIVERY DUE TO DAMAGE-(DIR)",
    "LDO": "LAST DATE OVER FOR SUBMISSION-(OTR)",
    "MIS": "LAST MILE MISROUTE-(OTR)",
    "NSP": "ADDRESS OK BUT NO SUCH PERSON-(CIR)",
    "NSR": "AREA NON SERVICEABLE-(DIR)",
    "PCC": "CUSTOMER WILL SELF COLLECT-(CIR)",
    "PNA": "RECEIVER NOT AVAILABLE-(CIR)",
    "PRF": "RECEIVER REFUSED DELIVERY(CIR)",
    "PSF": "RECEIVER SHIFTED FROM GIVEN ADDRESS-(CIR)",
    "REA": "RESTRICTED ENTRY-(OTR)",
    "LST": "CONSIGNMENT LOST-(OTR)",
    "UAT": "COULD NOT ATTEMPT-(DIR)",
    "COL": "COVID 19 - OFFICE CLOSED/DOOR LOCKED",
    "RWO": "RECEIVER WANTS OPEN DELIVERY-(CIR)",
    "RRT": "CONSIGNOR REFUSED RTO SHIPMENT-(CIR)",
    "LHL": "LOCAL HOLIDAY-(OTR)",
    "PWR": "PAPERWORK REQUIRED-(OTR)",
    "CNA": "COVID19 COULD NOT ATTEMPT",
    # Observed live 2026-09-10 on 7V105154631 and absent from DTDC's sheet.
    # Recorded so a real NDR is not reported as an unmapped code; the sheet is
    # evidently not exhaustive, and more will surface the same way.
    "KYC": "CUSTOMER REFUSED TO SHARE SCD/ NDC (live 2026-09-10, not in sheet)",
}

#: `statusFlag:false` bodies that mean "no such consignment", not "we failed".
_ABSENT = ("no data found",)


def _dt(date_s: Any, time_s: Any = None) -> datetime | None:
    """DTDC stamps are DDMMYYYY plus HHMM (or HH:MM:SS on the header).

    Parsed here rather than in dates.parse_dt on purpose: adding `%d%m%Y` to
    the shared format list would also swallow other carriers' bare `YYYYMMDD`,
    silently turning 20260913 into a date in the year 9013.
    """
    s = str(date_s or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        d = datetime.strptime(s, "%d%m%Y")
    except ValueError:
        return None
    t = str(time_s or "").strip().replace(":", "")
    if len(t) >= 4 and t[:4].isdigit():
        try:
            d = d.replace(hour=int(t[:2]), minute=int(t[2:4]))
        except ValueError:
            pass
    # DTDC scan times are branch-local wall clock, i.e. IST. Unlike Uniware --
    # whose timezone varies BY CARRIER (see awb_tracker/uniware_tz.py) -- this
    # is the carrier's own API and is IST throughout.
    return d.replace(tzinfo=IST)


def _ndr(remark: str | None, ctx: FetchContext, awb: str) -> str | None:
    """`DLK|OFFICE CLOSED OR DOOR LOCK-(CIR)` -> keep verbatim, flag if new."""
    text = (remark or "").strip()
    if not text:
        return None
    code = text.split("|", 1)[0].strip().upper()
    if code and code not in NDR_REASONS and ctx.dq is not None:
        ctx.dq.unmapped_status(ADAPTER_ID, f"ndr:{code}", awb, {"remark": text})
    return text


def parse(payload: dict[str, Any], awb: str, ctx: FetchContext) -> TrackingResult:
    """Pure parser -- testable against reference/fixtures/dtdc_*.json."""
    if not payload.get("statusFlag"):
        msg = ""
        for d in payload.get("errorDetails") or []:
            if isinstance(d, dict) and d.get("name") in ("strError", "error"):
                msg = str(d.get("value") or "")
        msg = msg or str(payload.get("status") or "")
        absent = any(a in msg.lower() for a in _ABSENT)
        return TrackingResult(
            awb=awb, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
            outcome=FetchOutcome.NOT_FOUND if absent else FetchOutcome.PARSE_ERROR,
            canonical_status=(CanonicalStatus.AWB_NOT_FOUND if absent
                              else CanonicalStatus.UNKNOWN),
            raw_status=msg[:160] or None,
            error=(None if absent
                   else (msg[:160] or "statusFlag false with no error given")),
            raw_payload=payload)

    head = payload.get("trackHeader") or {}

    events: list[TrackEvent] = []
    order: list[int] = []          # payload position, the tie-breaker below
    attempts = 0
    ndr: str | None = None
    for idx, s in enumerate(payload.get("trackDetails") or []):
        code = str(s.get("strCode") or "").strip().upper()
        mapped_ev = code in STATUS_MAP
        if not mapped_ev and code and ctx.dq is not None:
            ctx.dq.unmapped_status(ADAPTER_ID, code, awb, payload)
        if code in ("NONDLV", "RTONONDLV"):
            attempts += 1
            ndr = _ndr(s.get("sTrRemarks"), ctx, awb) or ndr
        events.append(TrackEvent(
            at=_dt(s.get("strActionDate"), s.get("strActionTime")),
            raw_status=code or str(s.get("strAction") or ""),
            canonical=STATUS_MAP.get(code, CanonicalStatus.UNKNOWN),
            location=(s.get("strOrigin") or "").strip() or None,
            # `sTrRemarks` -- lowercase s, capital T. Not a typo here.
            remark=(s.get("sTrRemarks") or "").strip() or None,
            raw_status_mapped=mapped_ev))
        order.append(idx)

    # Newest first, matching every other adapter (callers read events[0]).
    #
    # Sorted by timestamp rather than simply reversed, because DTDC's scan
    # times are only minute-resolution and ties are common: 7V105154707 stamps
    # BKD and OUTDLV both at 14:54. Payload position breaks the tie, since
    # DTDC emits scans in insert order -- without it a stable sort keeps the
    # tied pair oldest-first and `events[0]` can report BOOKED for a shipment
    # that is already out for delivery.
    #
    # Undated scans sort last so they can never masquerade as latest.
    _floor = datetime.min.replace(tzinfo=IST)
    events = [e for _, e in sorted(
        zip(order, events),
        key=lambda pair: (pair[1].at is not None, pair[1].at or _floor, pair[0]),
        reverse=True)]

    # Canonical comes from the LATEST SCAN, not the header: the scan vocabulary
    # is 71 codes against the header's five, so it separates RTO_DELIVERED from
    # DELIVERED and OUT_FOR_DELIVERY from REACHED_DESTINATION_HUB. The header
    # is the fallback for a response carrying no usable scan at all.
    latest = next((e for e in events
                   if e.canonical is not CanonicalStatus.UNKNOWN), None)
    if latest is not None:
        canonical, raw_status, mapped = latest.canonical, latest.raw_status, True
        status_at = latest.at
    else:
        raw_status = str(head.get("strStatus") or "").strip()
        key = raw_status.upper()
        mapped = key in HEADER_STATUS_MAP
        canonical = HEADER_STATUS_MAP.get(key, CanonicalStatus.UNKNOWN)
        if not mapped and raw_status and ctx.dq is not None:
            ctx.dq.unmapped_status(ADAPTER_ID, f"header:{raw_status}", awb, payload)
        status_at = None

    # DTDC's own commitment, and its revision. The revised date is what the
    # carrier is currently promising, so it wins when present -- but the
    # revision is itself a delay signal, and both survive in raw_payload.
    edd = (_dt(head.get("strRevExpectedDeliveryDate"))
           or _dt(head.get("strExpectedDeliveryDate")))

    header_attempts = str(head.get("strNoOfAttempts") or "").strip()
    if header_attempts.isdigit():
        attempts = max(attempts, int(header_attempts))

    return TrackingResult(
        awb=awb, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
        outcome=FetchOutcome.OK, canonical_status=canonical,
        raw_status=raw_status or None, raw_status_mapped=mapped,
        status_at=(status_at or _dt(head.get("strStatusTransOn"),
                                    head.get("strStatusTransTime"))),
        events=tuple(events),
        expected_delivery=edd,
        attempt_count=attempts or None,
        ndr_reason=(ndr if canonical in (CanonicalStatus.DELIVERY_ATTEMPT_FAILED,
                                         CanonicalStatus.ON_HOLD) else None),
        current_location=(events[0].location if events else None),
        destination=(head.get("strDestination") or "").strip() or None,
        recipient=((head.get("strRemarks") or "").strip() or None
                   if canonical == CanonicalStatus.DELIVERED else None),
        raw_payload=payload)


def _token(ctx: FetchContext) -> str | None:
    """Static customer key if present, else mint one from the login pair."""
    secrets = ctx.secrets or {}
    tok = (secrets.get("DTDC_ACCESS_TOKEN") or "").strip()
    if tok:
        return tok
    user = (secrets.get("DTDC_USERNAME") or "").strip()
    pwd = (secrets.get("DTDC_PASSWORD") or "").strip()
    if not (user and pwd):
        return None
    r = ctx.http.request(ADAPTER_ID, "GET", AUTH_API.format(u=user, p=pwd),
                         min_interval_s=SPEC.min_interval_s,
                         timeout=(SPEC.timeout_connect_s, SPEC.timeout_read_s))
    if r.status_code != 200:
        return None
    # The doc says 200 returns the "Token Access key" but never pins down
    # whether that is bare text or JSON, so accept either. UNVERIFIED: UC's
    # credential is a static key, so this branch has never run against DTDC.
    try:
        j = r.json()
        if isinstance(j, dict):
            for k in ("token", "accessToken", "access_token", "data"):
                if isinstance(j.get(k), str) and j[k].strip():
                    return j[k].strip()
        elif isinstance(j, str) and j.strip():
            return j.strip()
    except Exception:
        pass
    return (r.text or "").strip().strip('"') or None


def track(awbs: Sequence[str], ctx: FetchContext) -> list[TrackingResult]:
    token = _token(ctx)
    if not token:
        return [TrackingResult(
            awb=a, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
            outcome=FetchOutcome.AUTH_REQUIRED,
            canonical_status=CanonicalStatus.UNKNOWN,
            error="set DTDC_ACCESS_TOKEN (static key) or DTDC_USERNAME+"
                  "DTDC_PASSWORD; never enable DTDC by scraping") for a in awbs]

    def fetch_one(awb: str) -> TrackingResult:
        r = ctx.http.request(
            ADAPTER_ID, "POST", API,
            min_interval_s=SPEC.min_interval_s,
            timeout=(SPEC.timeout_connect_s, SPEC.timeout_read_s),
            headers={"Content-Type": "application/json",
                     "x-access-token": token, "Accept": "application/json"},
            json={"trkType": "cnno", "strcnno": awb, "addtnlDtl": "Y"},
            block_signatures=SPEC.block_signatures)
        if r.status_code in (401, 403):
            return TrackingResult(
                awb=awb, adapter_id=ADAPTER_ID, courier_code="",
                fetched_at=ctx.now, outcome=FetchOutcome.AUTH_REQUIRED,
                canonical_status=CanonicalStatus.UNKNOWN,
                http_status=r.status_code,
                error="DTDC rejected the access token")
        # 206 is DTDC's "no data for this consignment", not a partial read, so
        # it falls through to parse() like a 200 and becomes NOT_FOUND there.
        res = parse(r.json(), awb, ctx)
        res.http_status = r.status_code
        res.latency_ms = int(r.elapsed.total_seconds() * 1000)
        return res

    return run_loop(SPEC, awbs, ctx, fetch_one)
