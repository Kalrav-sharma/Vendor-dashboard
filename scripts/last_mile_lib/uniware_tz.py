"""Per-courier timezone calibration for Uniware's own timestamps.

Ported verbatim from awb-delivery-tracker's awb_tracker/uniware_tz.py.

MEASURED FINDING, 2026-09-07 -- Uniware's `Delivery Time` is NOT in a single
timezone. It stores whatever each carrier integration pushes:

  Shadowfax : UTC.  Verified on 10/10 AWBs at exactly +5h30m. Shadowfax's API
              returns 2026-08-10T14:00:07 and its own UI says "02:00 PM";
              Uniware recorded "2026-08-10 08:30:08".
  Blue Dart : IST.  Blue Dart's consumer page says "14 Aug 2026 16:37" and
              Uniware recorded "2026-08-14 16:37" -- exact match.
  Delhivery : IST.  Delhivery's statusDateTime matched Uniware to the second,
              and its `promiseDeliveryDate` of 23:59:59 is an end-of-day marker
              that only means anything in local time.
  Holisol   : unverified -- assumed IST until a delivered AWB confirms it.

Impact HERE: material, not cosmetic. This project compares an LSP's scan
time against Uniware's recorded time and applies hour-level thresholds
(stuck > 24h, out-for-delivery > 12h) and day-grain cohort assignment
(live/backlog/aged_out, in watchlist.py). An uncorrected 5h30m phantom gap
would manufacture false alerts on the majority of Shadowfax volume -- do
not skip this step to save an import.

So: normalise Uniware timestamps through here before ever comparing them
to an adapter's output, or using them for cohort/day-count math. Adapters
themselves always emit IST.
"""
from __future__ import annotations

from datetime import timedelta

from .dates import IST, parse_dt

#: adapter_id -> the timezone Uniware's timestamps are actually stored in.
UNIWARE_SOURCE_TZ: dict[str, str] = {
    "shadowfax": "UTC",     # verified, 10/10 at +5:30
    "bluedart": "IST",      # verified against Blue Dart's own page
    "delhivery": "IST",     # verified via promiseDeliveryDate end-of-day
    "holisol": "IST",       # ASSUMED -- no delivered AWB yet
    "dtdc": "IST",
    "porter": "IST",
    "not_trackable": "IST",
    "unknown": "IST",
}

#: Which of the above are assumptions rather than measurements.
ASSUMED: frozenset = frozenset({"holisol", "dtdc", "porter"})

UTC_OFFSET = timedelta(hours=5, minutes=30)


def to_ist(value, adapter_id: str):
    """Normalise a Uniware timestamp to real IST for the given courier."""
    dt = parse_dt(value) if not hasattr(value, "tzinfo") or isinstance(value, str) else value
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    if UNIWARE_SOURCE_TZ.get(adapter_id, "IST") == "UTC":
        # parse_dt labelled it IST; it was really UTC, so shift it forward.
        dt = dt + UTC_OFFSET
    return dt


def is_assumed(adapter_id: str) -> bool:
    return adapter_id in ASSUMED
