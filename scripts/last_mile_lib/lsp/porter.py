"""Porter adapter -- SHIPPED DISABLED.

Verified 2026-09-07: porter.in/track returns 404. Porter has no public
track-by-AWB page at all; consumer tracking lives inside their logged-in app,
and porter.in/api-integrations is a marketing page that exposes no endpoint or
auth scheme.

Porter is also on-demand intracity, so most of these are same-day trips where
an hourly poll is the wrong instrument -- a webhook is the right shape.

In-scope volume is 8 rows, so this stays disabled and simply reports itself.
"""
from __future__ import annotations

from typing import Sequence

from .base import (AdapterSpec, CanonicalStatus, FetchContext, FetchOutcome,
                   TrackingResult)

ADAPTER_ID = "porter"

SPEC = AdapterSpec(
    adapter_id=ADAPTER_ID,
    display_name="Porter",
    mode="official_api",
    enabled=False,
    requires_credentials=True,
    awb_pattern=r"^PTR_[A-Z]+_B2B\d+$",
    disabled_reason=("no public track-by-AWB page (porter.in/track -> 404); "
                     "needs Porter Enterprise API credentials or a webhook"),
    notes="Intracity on-demand: webhook suits it better than polling.",
)


def track(awbs: Sequence[str], ctx: FetchContext) -> list[TrackingResult]:
    return [TrackingResult(
        awb=a, adapter_id=ADAPTER_ID, courier_code="", fetched_at=ctx.now,
        outcome=FetchOutcome.AUTH_REQUIRED,
        canonical_status=CanonicalStatus.UNKNOWN,
        error=SPEC.disabled_reason) for a in awbs]
