"""Date parsing for a system where every source formats dates differently.

Uniware's own CSV mixes `dd-mm-yyyy hh:MM` and `yyyy-mm-dd hh:MM:ss` inside the
SAME column, so dates are treated as opaque strings in transit and parsed only
here. Each LSP adds its own: Blue Dart `09 Aug 2026` + `16:37`, Holisol
`7/13/2026 5:32:48 PM`, Delhivery ISO with microseconds, Shadowfax
`13 July 2026, Monday`.

Ported verbatim from the awb-delivery-tracker project's
awb_tracker/dates.py, which was itself ported from
sla-remap-skill/scripts/pipeline_io.py::parse_dt (2026-09-07) and extended
with the LSP formats verified live on 2026-09-07. Do not re-derive this --
it encodes real formats measured against real data, and re-deriving from
scratch is exactly the mistake that broke sync_last_mile_daily.py's first
draft (see that script's history / commit message for what went wrong).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# Order matters: the most specific and most common come first.
FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S.%f",     # delhivery 2026-08-14T07:50:29.002000
    "%Y-%m-%dT%H:%M:%S",        # shadowfax 2026-07-13T09:43:46
    "%Y-%m-%d %H:%M:%S",        # uniware
    "%Y-%m-%d %H:%M",
    "%d-%m-%Y %H:%M:%S",        # uniware, the other variant
    "%d-%m-%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %I:%M:%S %p",     # holisol 7/13/2026 5:32:48 PM
    "%m/%d/%Y %H:%M:%S",
    "%d %b %Y %H:%M",           # bluedart 14 Aug 2026 16:37
    "%d %b %Y",                 # bluedart 09 Aug 2026
    "%d %B %Y",                 # 13 July 2026
    "%Y-%m-%d",
    "%d-%m-%Y",
)


def parse_dt(value, assume_ist: bool = True):
    """Best-effort parse. Returns None rather than raising -- an unparseable
    date is a data-quality observation, not a crash."""
    if not value:
        return None
    s = str(value).strip()
    if not s or s in {"-", "NA", "N/A", "null", "None"}:
        return None
    if s.endswith("Z"):
        s = s[:-1]
    # "13 July 2026, Monday" -> "13 July 2026"
    if "," in s:
        head = s.split(",")[0].strip()
        if head:
            for fmt in ("%d %B %Y", "%d %b %Y"):
                try:
                    dt = datetime.strptime(head, fmt)
                    return dt.replace(tzinfo=IST) if assume_ist else dt
                except ValueError:
                    pass
    for fmt in FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=IST) if assume_ist else dt
        except ValueError:
            continue
    return None


def now_ist():
    return datetime.now(IST)
