"""Data-quality sink -- the mechanism that stops an unmapped status rotting.

Requirement this implements: a raw LSP status we do not recognise must never be
quietly folded into UNKNOWN. A wrong guess silently corrupts on-time %, which
is the one number the whole system exists to report.

So on a mapping miss, five things happen together:
  1. the result carries UNKNOWN with the verbatim raw status preserved
  2. the (adapter, raw_status) pair is upserted here with counts + sample AWBs
  3. the raw payload is force-archived even though nothing changed, so the
     evidence still exists when someone investigates next week
  4. the run summary prints a loud block naming the new values
  5. the dashboard EXCLUDES those AWBs from its status KPIs and shows them in a
     separate, always-visible `unclassified` tile

Step 5 is the structural guarantee: an unmapped status degrades a number
somebody is looking at, so it cannot be ignored indefinitely.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dates import now_ist


@dataclass
class DQItem:
    kind: str                 # unmapped_status | unmapped_courier | awb_pattern | missing_awb
    key: str                  # "adapter:raw_status" or the courier code
    adapter: str | None
    detail: str
    first_seen: str
    last_seen: str
    occurrences: int = 0
    sample_awbs: list[str] = field(default_factory=list)
    payload_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "key": self.key, "adapter": self.adapter,
            "detail": self.detail, "first_seen": self.first_seen,
            "last_seen": self.last_seen, "occurrences": self.occurrences,
            "sample_awbs": self.sample_awbs[:5], "payload_path": self.payload_path,
        }


class DQSink:
    """Accumulates data-quality findings for one run, then persists them."""

    def __init__(self, state_dir: str | Path = "state",
                 archive_payloads: bool = True) -> None:
        self.state_dir = Path(state_dir)
        self.archive_payloads = archive_payloads
        self.items: dict[str, DQItem] = {}
        self._load()

    # ------------------------------------------------------------- persistence
    @property
    def _path(self) -> Path:
        return self.state_dir / "dq.json"

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            for raw in json.loads(self._path.read_text(encoding="utf-8")):
                item = DQItem(
                    kind=raw["kind"], key=raw["key"], adapter=raw.get("adapter"),
                    detail=raw.get("detail", ""), first_seen=raw["first_seen"],
                    last_seen=raw["last_seen"], occurrences=raw.get("occurrences", 0),
                    sample_awbs=list(raw.get("sample_awbs") or []),
                    payload_path=raw.get("payload_path"))
                self.items[f"{item.kind}|{item.key}"] = item
        except Exception:
            # A corrupt DQ file must never take down a tracking run.
            self.items = {}

    def flush(self) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = [i.to_dict() for i in sorted(
            self.items.values(), key=lambda x: -x.occurrences)]
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return self._path

    # ------------------------------------------------------------------ record
    def _upsert(self, kind: str, key: str, adapter: str | None,
                detail: str, awb: str | None) -> DQItem:
        stamp = now_ist().isoformat()
        ident = f"{kind}|{key}"
        item = self.items.get(ident)
        if item is None:
            item = DQItem(kind=kind, key=key, adapter=adapter, detail=detail,
                          first_seen=stamp, last_seen=stamp)
            self.items[ident] = item
        item.last_seen = stamp
        item.occurrences += 1
        if awb and awb not in item.sample_awbs and len(item.sample_awbs) < 5:
            item.sample_awbs.append(awb)
        return item

    def unmapped_status(self, adapter: str, raw_status: str, awb: str,
                        payload: Any = None) -> None:
        item = self._upsert("unmapped_status", f"{adapter}:{raw_status}",
                            adapter, f"unmapped raw status {raw_status!r}", awb)
        # Force-archive the evidence even though nothing "changed".
        if self.archive_payloads and payload is not None and not item.payload_path:
            try:
                d = self.state_dir / "raw" / now_ist().strftime("%Y-%m-%d") / adapter
                d.mkdir(parents=True, exist_ok=True)
                safe = "".join(ch if ch.isalnum() else "_" for ch in raw_status)[:60]
                p = d / f"unmapped_{safe}_{awb}.json"
                p.write_text(json.dumps(payload, indent=2, default=str),
                             encoding="utf-8")
                item.payload_path = str(p)
            except Exception:
                pass

    def unmapped_courier(self, courier_code: str, awb: str) -> None:
        self._upsert("unmapped_courier", courier_code or "<blank>", None,
                     f"no adapter rule matches courier {courier_code!r}", awb)

    def awb_pattern_mismatch(self, adapter: str, awb: str, courier: str) -> None:
        # Blue Dart (11 digits) and Delhivery (14) are both numeric, so this
        # usually means the courier field itself is wrong, not the AWB.
        self._upsert("awb_pattern", f"{adapter}:{courier}", adapter,
                     f"AWB does not match {adapter} pattern; courier may be misassigned",
                     awb)

    def missing_awb(self, courier: str, order_code: str) -> None:
        self._upsert("missing_awb", courier or "<blank>", None,
                     "dispatched shipment carries no usable tracking number",
                     order_code)

    # ------------------------------------------------------------------ report
    def by_kind(self, kind: str) -> list[DQItem]:
        return sorted((i for i in self.items.values() if i.kind == kind),
                      key=lambda x: -x.occurrences)

    def summary(self, max_per_kind: int = 15) -> dict[str, Any]:
        """Compact enough for the <2 KB run summary the agent reads."""
        out: dict[str, Any] = {}
        for kind in ("unmapped_status", "unmapped_courier", "awb_pattern", "missing_awb"):
            items = self.by_kind(kind)
            if not items:
                continue
            out[kind] = {
                "distinct": len(items),
                "total_occurrences": sum(i.occurrences for i in items),
                "top": [{"key": i.key, "n": i.occurrences,
                         "samples": i.sample_awbs[:2]}
                        for i in items[:max_per_kind]],
            }
        return out

    def loud_block(self) -> str:
        """The run-summary text that makes a new unmapped value impossible to miss."""
        lines: list[str] = []
        for kind, label in (("unmapped_status", "UNMAPPED STATUSES"),
                            ("unmapped_courier", "UNMAPPED COURIER CODES"),
                            ("awb_pattern", "AWB PATTERN MISMATCHES"),
                            ("missing_awb", "MISSING AWBs")):
            items = self.by_kind(kind)
            if not items:
                continue
            head = ", ".join(f"{i.key} x{i.occurrences}" for i in items[:5])
            more = f" (+{len(items) - 5} more)" if len(items) > 5 else ""
            lines.append(f"  !! {len(items)} {label}: {head}{more}")
        if lines:
            lines.append("     -> add them to the adapter STATUS_MAP / courier_map.json")
        return "\n".join(lines)
