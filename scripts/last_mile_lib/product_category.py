"""Product category over Uniware's free-text `Item Type Name`.

There is no formal SKU-category taxonomy in the Sale Orders export --
`Item Type Name` and `SKU Name` hold the exact same value (confirmed live
2026-09-28: both are just the item's own product name, e.g. "Native M1 Pro
RO water purifier", "NATIVE LOCK PRO - BLUE (BOM)", "1 Metre White Pipe,
Pack of 1"). This module classifies that free text into RO / Locks / Spares
/ Refresh for the Alerts tab's Category column.

Rule, user decision 2026-09-29: RO means the purifier UNIT itself -- only a
name containing "purifier" counts (RO accessories/consumables like membrane,
filter housing, health booster are deliberately NOT RO under this rule).
Locks is anything naming "lock" (matches Blue Dart's BD_SMARTLOCKS_* courier
variant independently -- the two signals agree). Refresh is anything naming
"refresh" -- unverified against real data, no such SKU has been observed
live yet, but the rule is here so one starts working the moment it appears.
Spares is everything else -- not a keyword match, the deliberate catch-all,
covering both the small-parts volume already observed ("1 Metre White Pipe",
"NATIVE ESF Spanner", "Battery M3") and anything else that isn't RO/Locks/
Refresh, including non-obvious volume like "Black T-Shirt XL" (observed
live in the same export).
"""
from __future__ import annotations

import re

_REFRESH_RE = re.compile(r"refresh", re.IGNORECASE)
_LOCK_RE = re.compile(r"\block\b", re.IGNORECASE)
_RO_RE = re.compile(r"purifier", re.IGNORECASE)

CATEGORIES = ("RO", "Locks", "Spares", "Refresh")


def classify(item_type_name: str | None) -> str:
    """Item Type Name / SKU Name -> RO | Locks | Refresh | Spares (catch-all)."""
    name = (item_type_name or "").strip()
    if _REFRESH_RE.search(name):
        return "Refresh"
    if _LOCK_RE.search(name):
        return "Locks"
    if _RO_RE.search(name):
        return "RO"
    return "Spares"
