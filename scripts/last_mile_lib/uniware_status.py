"""Uniware `Shipping Tracking Status` -> our canonical status.

Mined from real in-scope data (PB-UC-GGN, 30 days, 2026-09-07) rather than
transcribed from Unicommerce's 89-value enum: only 15 values actually occur,
and mapping what occurs beats mapping what might.

    17955  DELIVERED_TO_CUSTOMER          686  RTO_INITIATED
     7137  RTO_IN_TRANSIT                 101  UNDELIVERED
     2757  IN_TRANSIT                      79  RTO_DELIVERED_TO_SELLER
       44  PICKED_UP                       27  DELIVERED_SHIPMENT_DELIVERED
       19  HELD                            16  DELAYED
        7  STATUS_NOT_DEFINED               4  OUT_FOR_PICKUP
        4  PICKUP_PENDING                   2  OUT_FOR_DELIVERY
        1  REACHED_AT_DESTINATION

The rest of the published enum is included so a value that appears later maps
correctly instead of arriving as UNKNOWN, but anything genuinely unrecognised
still surfaces as a data-quality item -- the same rule the LSP adapters follow.
"""
from __future__ import annotations

from .lsp.base import CanonicalStatus as C

#: Uniware canonical -> ours.
MAP: dict[str, C] = {
    # delivered
    "DELIVERED_TO_CUSTOMER": C.DELIVERED,
    "DELIVERED_SHIPMENT_DELIVERED": C.DELIVERED,
    "DELIVERED": C.DELIVERED,
    # forward movement
    "MANIFESTED": C.MANIFESTED,
    "COURIER_ASSIGNED": C.MANIFESTED,
    "PICKUP_PENDING": C.MANIFESTED,
    "OUT_FOR_PICKUP": C.MANIFESTED,
    "PICKED_UP": C.PICKED_UP,
    "IN_TRANSIT": C.IN_TRANSIT,
    "REACHED_AT_DESTINATION": C.REACHED_DESTINATION_HUB,
    "OUT_FOR_DELIVERY": C.OUT_FOR_DELIVERY,
    # trouble
    "UNDELIVERED": C.DELIVERY_ATTEMPT_FAILED,
    "PENDING_CONSIGNEE_NOT_AVAILABLE": C.DELIVERY_ATTEMPT_FAILED,
    "PENDING_CONSIGNEE_REFUSED": C.DELIVERY_ATTEMPT_FAILED,
    "HELD": C.ON_HOLD,
    "ON_HOLD": C.ON_HOLD,
    # DELAYED is Uniware's own "late but still moving" marker. It maps to
    # IN_TRANSIT so it never reads as a terminal state, and is separately
    # flagged as a carrier-declared delay by the alert engine.
    "DELAYED": C.IN_TRANSIT,
    # returns
    "RTO_INITIATED": C.RTO_INITIATED,
    "RTO_IN_TRANSIT": C.RTO_IN_TRANSIT,
    "RTO_DELIVERED_TO_SELLER": C.RTO_DELIVERED,
    "RTO_DELIVERED": C.RTO_DELIVERED,
    # loss
    "SHIPMENT_LOST": C.LOST_OR_DAMAGED,
    "SHIPMENT_DAMAGED": C.LOST_OR_DAMAGED,
    "SHIPMENT_DESTROYED": C.LOST_OR_DAMAGED,
    "LOST": C.LOST_OR_DAMAGED,
    # cancelled
    "CANCELLED": C.CANCELLED,
    "SHIPMENT_CANCELLED": C.CANCELLED,
    # explicit unknown -- Uniware saying "I don't know" is information, and
    # must not be confused with us failing to map a value we do not recognise.
    "STATUS_NOT_DEFINED": C.UNKNOWN,
}

#: Values where Uniware itself is declaring a delay.
CARRIER_DECLARED_DELAY: frozenset[str] = frozenset({"DELAYED"})


def to_canonical(uniware_status: str | None) -> tuple[C, bool]:
    """Returns (canonical, mapped). `mapped=False` means surface a DQ item."""
    raw = (uniware_status or "").strip().upper()
    if not raw:
        return C.UNKNOWN, True     # absent is not unmapped; it is simply absent
    if raw in MAP:
        return MAP[raw], True
    return C.UNKNOWN, False


def is_declared_delay(uniware_status: str | None) -> bool:
    return (uniware_status or "").strip().upper() in CARRIER_DECLARED_DELAY


#: `Shipping Package Status Code` values that mean the shipment has turned
#: around. This is a SECOND, independent column from `Shipping Tracking Status`,
#: and it is the only place `RETURN_EXPECTED` ever appears.
RETURN_PACKAGE_CODES: frozenset[str] = frozenset({"RETURNED", "RETURN_EXPECTED"})

#: Tracking-status values in the return family.
RETURN_TRACKING_STATUS: frozenset[str] = frozenset({
    "RTO_INITIATED", "RTO_IN_TRANSIT",
    "RTO_DELIVERED", "RTO_DELIVERED_TO_SELLER",
})


def is_return(tracking_status: str | None,
              package_status_code: str | None = None) -> bool:
    """Whether Uniware considers this shipment a return, by EITHER column.

    Measured 2026-09-09 at AWB grain: the two columns agree almost perfectly --
    exactly one shipment had a return package code (`RETURN_EXPECTED`) while its
    tracking status said something else (`STATUS_NOT_DEFINED`). So the package
    code earns its place here as a safety net for a blank or undefined tracking
    status, not as a primary signal. Checking both is what stops that one
    shipment sitting on the board as a forward delivery.
    """
    if (tracking_status or "").strip().upper() in RETURN_TRACKING_STATUS:
        return True
    return (package_status_code or "").strip().upper() in RETURN_PACKAGE_CODES
