"""Uniware tracking-status helpers.

Ported from awb-delivery-tracker's awb_tracker/uniware_status.py -- only
the return-detection part, which is what the daily watchlist build needs.
The full 89-value status enum mapping lives with the hourly job, which is
the thing that actually interprets live courier states.
"""
from __future__ import annotations

#: Package-status codes in the return family.
RETURN_PACKAGE_CODES: frozenset = frozenset({"RETURNED", "RETURN_EXPECTED"})

#: Tracking-status values in the return family.
RETURN_TRACKING_STATUS: frozenset = frozenset({
    "RTO_INITIATED", "RTO_IN_TRANSIT",
    "RTO_DELIVERED", "RTO_DELIVERED_TO_SELLER",
})


def is_return(tracking_status, package_status_code=None) -> bool:
    """Whether Uniware considers this shipment a return, by EITHER column.

    Measured 2026-09-09 at AWB grain: the two columns agree almost perfectly --
    exactly one shipment had a return package code (`RETURN_EXPECTED`) while its
    tracking status said something else (`STATUS_NOT_DEFINED`). So the package
    code earns its place here as a safety net for a blank or undefined tracking
    status, not as a primary signal. Checking both is what stops that one
    shipment sitting on the board as a forward delivery.

    Why this matters for the daily build: a return keeps item_status
    DISPATCHED, so CLOSED_ITEM_STATUS alone never catches one. Without this
    check the rolling 45-day export re-adds every return to the watchlist
    every single morning, forever.
    """
    if (tracking_status or "").strip().upper() in RETURN_TRACKING_STATUS:
        return True
    return (package_status_code or "").strip().upper() in RETURN_PACKAGE_CODES
