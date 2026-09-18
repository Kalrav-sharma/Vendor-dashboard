"""Per-courier timezone calibration for Uniware's own timestamps.

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

Impact on DAY-grain TAT: none, measured. Across 16,316 Shadowfax deliveries the
stored hours span 03:00-17:00 UTC (08:30-22:30 IST); none fall in the
18:30-23:59 UTC band that would push the real IST date to the next day. So the
existing SLA TAT math is not wrong because of this.

Impact HERE: material. This project compares an LSP's scan time against
Uniware's recorded time and applies hour-level thresholds (stuck > 24h,
out-for-delivery > 12h). An uncorrected 5h30m phantom gap would manufacture
false STUCK and STATUS_MISMATCH alerts on 89% of volume.

So: normalise Uniware timestamps through here before ever comparing them to an
adapter's output. Adapters themselves always emit IST.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .dates import IST, parse_dt

#: adapter_id -> the timezone Uniware's timestamps are actually stored in.
UNIWARE_SOURCE_TZ: dict[str, str] = {
    "shadowfax": "UTC",     # verified, 10/10 at +5:30
    "bluedart": "IST",      # verified against Blue Dart's own page
    "delhivery": "IST",     # verified via promiseDeliveryDate end-of-day
    "holisol": "IST",       # ASSUMED -- no delivered AWB yet
    "dtdc": "IST",          # ASSUMED -- adapter disabled anyway
    "porter": "IST",        # ASSUMED -- adapter disabled anyway
    "not_trackable": "IST",
}

#: Which of the above are assumptions rather than measurements. Anything listed
#: here should be re-verified once a delivered AWB for that courier exists, and
#: is surfaced on the dashboard rather than presented as fact.
ASSUMED: frozenset[str] = frozenset({"holisol", "dtdc", "porter"})

UTC_OFFSET = timedelta(hours=5, minutes=30)


def to_ist(value: str | datetime | None, adapter_id: str) -> datetime | None:
    """Normalise a Uniware timestamp to real IST for the given courier."""
    dt = parse_dt(value) if not isinstance(value, datetime) else value
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


def calibration_note(adapter_id: str) -> str:
    tz = UNIWARE_SOURCE_TZ.get(adapter_id, "IST")
    suffix = " (assumed, unverified)" if is_assumed(adapter_id) else " (verified)"
    return f"Uniware stores {adapter_id} timestamps in {tz}{suffix}"
