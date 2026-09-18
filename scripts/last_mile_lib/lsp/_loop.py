"""Shared per-AWB fetch loop.

Every adapter needs identical handling for budget exhaustion, block pages,
circuit-open and unexpected exceptions. Doing that once here keeps the adapters
to just their endpoint and their parser -- and, more importantly, guarantees
all four behave the same way when an LSP misbehaves.
"""
from __future__ import annotations

from typing import Callable, Sequence

from .base import (AdapterSpec, CanonicalStatus, FetchContext, FetchOutcome,
                   TrackingResult)
from .http import BlockedError, CircuitOpen


def _err(awb: str, spec: AdapterSpec, ctx: FetchContext,
         outcome: FetchOutcome, error: str,
         status: CanonicalStatus = CanonicalStatus.UNKNOWN) -> TrackingResult:
    return TrackingResult(
        awb=awb, adapter_id=spec.adapter_id, courier_code="",
        fetched_at=ctx.now, outcome=outcome, canonical_status=status, error=error)


def run_loop(spec: AdapterSpec, awbs: Sequence[str], ctx: FetchContext,
             fetch_one: Callable[[str], TrackingResult]) -> list[TrackingResult]:
    """Call `fetch_one` per AWB with uniform failure handling.

    A block or an open circuit ABORTS the remaining AWBs for this adapter --
    continuing would just hammer an endpoint that has already said no. The
    unfetched ones come back as deferred so the run reports them rather than
    pretending they were checked.
    """
    out: list[TrackingResult] = []
    aborted_at: int | None = None

    # Honour the adapter's own per-run cap here rather than in each adapter.
    # Only Blue Dart implemented it, which is how Shadowfax came to attempt
    # 5,167 calls against a rolling quota it cannot possibly satisfy.
    over: list[str] = []
    # A per-run override (hourly_track --cap) wins over the adapter own
    # ceiling; without one, the adapter ceiling still applies. Before
    # this, --cap only trimmed the input list and the SPEC cap bit
    # independently, so --cap above the ceiling silently did nothing:
    # asking for 600 still stopped at exactly 400 on 2026-09-14.
    cap = (ctx.max_awbs_override or {}).get(spec.adapter_id,
                                            spec.max_awbs_per_run)
    if cap is not None and len(awbs) > cap:
        awbs, over = list(awbs)[:cap], list(awbs)[cap:]

    for i, awb in enumerate(awbs):
        if ctx.out_of_budget():
            aborted_at = i
            break
        try:
            out.append(fetch_one(awb))
        except BlockedError as exc:
            out.append(_err(awb, spec, ctx, FetchOutcome.BLOCKED, str(exc)))
            aborted_at = i + 1
            break
        except CircuitOpen as exc:
            out.append(_err(awb, spec, ctx, FetchOutcome.TRANSIENT_ERROR, str(exc)))
            aborted_at = i + 1
            break
        except Exception as exc:
            out.append(_err(awb, spec, ctx, FetchOutcome.TRANSIENT_ERROR,
                            f"{type(exc).__name__}: {exc}"))

    if aborted_at is not None:
        for awb in awbs[aborted_at:]:
            out.append(_err(awb, spec, ctx, FetchOutcome.SKIPPED_BUDGET,
                            "deferred: adapter aborted earlier this run"))
    for awb in over:
        out.append(_err(awb, spec, ctx, FetchOutcome.SKIPPED_BUDGET,
                        f"deferred: over {spec.adapter_id} cap={cap}"))
    return out
