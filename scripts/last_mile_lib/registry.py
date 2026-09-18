"""Courier-code -> adapter resolution, driven by config/courier_map.json.

Ported from awb-delivery-tracker's awb_tracker/lsp/registry.py, TRIMMED to
just the resolution half. The original also imports one Python module per
carrier to do the actual live polling; those adapters belong with the
hourly job and are not needed (and do not exist here) to classify a
shipment during the daily watchlist build.

Resolution is exact -> regex -> longest-matching-prefix, which makes the
rule list order-independent so a future `SFX_BOMBAY_ANDHERI` or
`BD_SMARTLOCKS_PUN` lands correctly with no edit at all.

An unrecognised courier code resolves to `unknown` and is REPORTED as a
data-quality item. It is never silently dropped -- a courier we cannot
place is volume we are not tracking, and that has to be visible.

Keys on `Shipping Courier`, NEVER `Shipping provider`: DTDC's 15
DTDC_RAFTAAR_<location> provider variants only collapse to a single
`DTDC` value in the Courier column. Do not "simplify" this to string
matching on the provider field.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "courier_map.json"


@dataclass(frozen=True)
class Resolution:
    adapter_id: str
    matched_by: str          # exact | regex | prefix | fallback
    rule_value: str | None
    reason: str | None = None

    @property
    def is_unknown(self) -> bool:
        return self.adapter_id == "unknown"


@lru_cache(maxsize=1)
def load_map(path: str | None = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache(maxsize=4096)
def resolve(courier_code: str | None) -> Resolution:
    """Map a raw `Shipping Courier` value onto an adapter id."""
    code = (courier_code or "").strip()
    upper = code.upper()
    cfg = load_map()
    rules = cfg.get("rules", [])
    fallback = cfg.get("on_unmapped", {}) or {}

    for r in rules:
        if r.get("match") == "exact" and upper == str(r.get("value", "")).upper():
            return Resolution(r["adapter"], "exact", r.get("value"), r.get("reason"))

    for r in rules:
        if r.get("match") == "regex" and re.match(str(r.get("value")), upper):
            return Resolution(r["adapter"], "regex", r.get("value"), r.get("reason"))

    # Longest prefix wins, so a specific rule always beats a generic one
    # regardless of where each sits in the file.
    best = None
    for r in rules:
        if r.get("match") != "prefix":
            continue
        val = str(r.get("value", "")).upper()
        if val and upper.startswith(val) and (best is None or len(val) > best[0]):
            best = (len(val), r)
    if best:
        r = best[1]
        return Resolution(r["adapter"], "prefix", r.get("value"), r.get("reason"))

    return Resolution(fallback.get("adapter", "unknown"), "fallback", None,
                      fallback.get("reason"))


#: Adapters whose volume NEVER enters the dashboard -- copied from the source
#: project's config.json, where it records a user decision (2026-09-09):
#: "dont track Not trackable (SELF), Porter and Unknown (Easy_GO, Ripplr),
#: they should never enter the dashboard".
#:
#: Shipments resolving to one of these are cohorted 'excluded' at intake, so
#: they are absent from alerts, carrier scorecards, worst lanes and every
#: queue -- not merely flagged. They survive as ONE reconciling figure in the
#: coverage funnel, because a funnel that does not account for its own input
#: would let the board claim coverage of a shrunken denominator.
#:
#: DTDC is deliberately NOT here: its volume is real and its performance is
#: still measurable. `unknown` IS here -- an unmapped courier is still
#: reported as a data-quality item, it just does not become an alert.
EXCLUDED_ADAPTERS: frozenset = frozenset({"not_trackable", "porter", "unknown"})
