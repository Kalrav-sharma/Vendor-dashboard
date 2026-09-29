"""Best-effort product category over Uniware's free-text `Item Type Name`.

There is no formal SKU-category taxonomy in the Sale Orders export --
`Item Type Name` and `SKU Name` hold the exact same value (confirmed live
2026-09-28: both are just the item's own product name, e.g. "Native M1 Pro
RO water purifier", "NATIVE LOCK PRO - BLUE (BOM)", "1 Metre White Pipe,
Pack of 1"). This module classifies that free text into RO / Locks /
Spares / Refresh for the Alerts tab's Category column.

User decision 2026-09-28: anything a keyword can't confidently place falls
to "Other" rather than being force-fit into one of the four -- "Black
T-Shirt XL" was observed live in the same export and is real apparel
volume this taxonomy was never meant to cover, not a data-quality gap.

Order matters: Refresh is checked first because a bundled refresh kit's
name could plausibly also mention "RO" or "filter", and the more specific
signal should win. No real "refresh"-named SKU has been observed yet --
this rule is here so one starts working the moment such a SKU appears,
without needing a code change.
"""
from __future__ import annotations

import re

_REFRESH_RE = re.compile(r"refresh", re.IGNORECASE)
_LOCK_RE = re.compile(r"\block\b", re.IGNORECASE)
# User decision 2026-09-29: RO means the purifier UNIT itself -- only a name
# containing "purifier" counts. RO accessories/consumables (membrane, filter
# housing, health booster) are deliberately NOT RO under this rule, even
# though they're RO-related; they fall through to Spares/Other instead.
_RO_RE = re.compile(r"purifier", re.IGNORECASE)
# Enumerated, not a catch-all -- these are the specific spare-part terms
# observed live ("1 Metre White Pipe, Pack of 1", "1/2\" thread adapter...",
# "Battery M3", "NATIVE ESF Spanner", "S Bracket Lexcru 10 (VERGIN-BLACK)",
# "NATIVE Plastic Material Dummy"). Deliberately narrow so an unrecognised
# name falls to "Other" instead of silently landing in "Spares".
_SPARES_RE = re.compile(
    r"\bpipe\b|adapter|spanner|\bbracket\b|\bbattery\b|\bdummy\b|quick fit",
    re.IGNORECASE)

CATEGORIES = ("RO", "Locks", "Spares", "Refresh", "Other")


def classify(item_type_name: str | None) -> str:
    """Item Type Name / SKU Name -> RO | Locks | Spares | Refresh | Other."""
    name = (item_type_name or "").strip()
    if not name:
        return "Other"
    if _REFRESH_RE.search(name):
        return "Refresh"
    if _LOCK_RE.search(name):
        return "Locks"
    if _RO_RE.search(name):
        return "RO"
    if _SPARES_RE.search(name):
        return "Spares"
    return "Other"
