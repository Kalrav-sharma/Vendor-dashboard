"""Courier-code -> adapter resolution, driven by config/courier_map.json.

Adding a carrier is one new module plus one JSON rule; nothing in the core
changes. Resolution is exact -> regex -> longest-matching-prefix, which makes
the rule list order-independent so a future `SFX_BOMBAY_ANDHERI` or
`BD_SMARTLOCKS_PUN` lands correctly with no edit at all.

An unrecognised courier code resolves to `unknown` and is REPORTED as a
data-quality item. It is never silently dropped -- a courier we cannot place is
volume we are not tracking, and that has to be visible.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any

from .base import AdapterSpec

# Rehomed for this repo: the config sits at last_mile_lib/config/, one level
# up from lsp/, rather than at the source project root two levels up.
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "courier_map.json"

#: Module name per adapter id. Kept explicit rather than auto-discovered so an
#: import error is a loud failure instead of a silently missing carrier.
ADAPTER_MODULES: dict[str, str] = {
    "shadowfax": "last_mile_lib.lsp.shadowfax",
    "bluedart": "last_mile_lib.lsp.bluedart",
    "delhivery": "last_mile_lib.lsp.delhivery",
    "holisol": "last_mile_lib.lsp.holisol",
    "dtdc": "last_mile_lib.lsp.dtdc",
    "porter": "last_mile_lib.lsp.porter",
    "not_trackable": "last_mile_lib.lsp.not_trackable",
}


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
def load_map(path: str | None = None) -> dict[str, Any]:
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
    best: tuple[int, dict[str, Any]] | None = None
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


@lru_cache(maxsize=32)
def get_adapter(adapter_id: str) -> Any | None:
    """Import an adapter module. Returns None for `unknown`."""
    mod_name = ADAPTER_MODULES.get(adapter_id)
    if not mod_name:
        return None
    return import_module(mod_name)


def get_spec(adapter_id: str) -> AdapterSpec | None:
    mod = get_adapter(adapter_id)
    return getattr(mod, "SPEC", None) if mod else None


def group_by_adapter(items: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """Group (awb, courier_code) pairs by resolved adapter id.

    Grouping is what lets each adapter apply its own concurrency and rate cap,
    and what makes `unknown` volume countable.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for awb, courier in items:
        out.setdefault(resolve(courier).adapter_id, []).append((awb, courier))
    return out


def validate_awb(awb: str, adapter_id: str) -> bool | None:
    """Check an AWB against its adapter's pattern.

    Returns None when the adapter declares no pattern. A False here is a
    data-quality signal, not a reason to skip the call: Blue Dart (11 digits)
    and Delhivery (14 digits) are both numeric, so a length mismatch usually
    means the courier field itself is wrong.
    """
    spec = get_spec(adapter_id)
    if not spec or not spec.awb_pattern:
        return None
    return bool(re.match(spec.awb_pattern, (awb or "").strip()))


def coverage_table() -> list[dict[str, Any]]:
    """What the dashboard's coverage view renders, derived from the same config
    and the adapters' own `enabled` flags -- so stated coverage cannot drift
    from the code."""
    rows: list[dict[str, Any]] = []
    for adapter_id in sorted(set(r["adapter"] for r in load_map().get("rules", []))):
        spec = get_spec(adapter_id)
        rows.append({
            "adapter": adapter_id,
            "display_name": spec.display_name if spec else adapter_id,
            "mode": spec.mode if spec else "unresolved",
            "enabled": bool(spec.enabled) if spec else False,
            "disabled_reason": spec.disabled_reason if spec else
            "no adapter module for this courier code",
        })
    return rows
