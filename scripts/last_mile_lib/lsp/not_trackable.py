"""The explicit "there is no LSP to ask" adapter.

SELF (in-house fleet) and a blank courier are the two largest buckets in the
raw data. Skipping them silently would make the coverage denominator a lie, so
they resolve to a real adapter that returns NOT_TRACKABLE with a reason. The
dashboard renders each as its own labelled bucket -- notably "no courier
assigned", which is a genuine ops finding rather than a rounding error.
"""
from __future__ import annotations

from typing import Sequence

from .base import (AdapterSpec, CanonicalStatus, FetchContext, FetchOutcome,
                   TrackingResult)

ADAPTER_ID = "not_trackable"

SPEC = AdapterSpec(
    adapter_id=ADAPTER_ID,
    display_name="Not trackable",
    mode="not_trackable",
    enabled=True,
    disabled_reason=None,
    notes="SELF = in-house fleet; blank = no courier assigned.",
)


def track(awbs: Sequence[str], ctx: FetchContext) -> list[TrackingResult]:
    return [TrackingResult(
        awb=a, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
        outcome=FetchOutcome.NOT_TRACKABLE,
        canonical_status=CanonicalStatus.NOT_TRACKABLE) for a in awbs]
