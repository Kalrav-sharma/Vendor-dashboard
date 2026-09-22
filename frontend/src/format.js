// Shared formatting/status helpers -- ported from the legacy
// docs/assets/app-common.js. escapeHtml() from that file has no
// equivalent here: Vue's templates auto-escape interpolated text, so
// there's nothing to port for that specific concern.

export const TERMINAL_STATUSES = new Set(["COMPLETE", "REJECTED", "CANCELLED", "CLOSED"]);

// Rejected and not-yet-approved POs are hidden from both the vendor and
// admin views entirely (Kalrav's explicit requirement) -- CREATED is
// Uniware's "drafted, not yet approved" status.
export const HIDDEN_STATUSES = new Set(["REJECTED", "CREATED"]);

export const STATUS_META = {
  COMPLETE: ["Complete", "good"],
  APPROVED: ["Approved · open", "open"],
  CREATED: ["Created · open", "open"],
  REJECTED: ["Rejected", "critical"],
  CANCELLED: ["Cancelled", "muted"],
};

export function fmtNum(n) {
  return (n === null || n === undefined) ? "–" : Number(n).toLocaleString("en-IN");
}

export function fmtMoney(n) {
  return (n === null || n === undefined) ? "–" : "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function fmtDate(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }) + ", " +
         d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

// For a plain date (no meaningful time component) like an OCR-extracted
// invoice due date -- "YYYY-MM-DD" in, "20 Sep 2026" out.
export function fmtDateOnly(dateStr) {
  if (!dateStr) return "–";
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return dateStr; // fall back to the raw string rather than "Invalid Date"
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function statusLabel(status) {
  return (STATUS_META[status] || [status || "Unknown"])[0];
}

export function statusClass(status) {
  return (STATUS_META[status] || [null, "muted"])[1];
}

// Invoice-vs-PO/GRN reconciliation status (see check-invoice-match Edge
// Function) -- same [label, chip color] pattern as STATUS_META above.
export const MATCH_STATUS_META = {
  pending: ["Checking…", "muted"],
  matched: ["Matches PO/GRN", "good"],
  mismatch: ["Mismatch found", "critical"],
  needs_review: ["GRN Pending", "open"],
  error: ["Check failed", "critical"],
};

export function matchStatusLabel(status) {
  return (MATCH_STATUS_META[status] || MATCH_STATUS_META.pending)[0];
}

export function matchStatusClass(status) {
  return (MATCH_STATUS_META[status] || MATCH_STATUS_META.pending)[1];
}

// Bluedart's raw StatusType code from shipment_tracking -- same
// [label, chip color] pattern as STATUS_META above.
export const BLUEDART_STATUS_META = {
  DL: ["Delivered", "good"],
  IT: ["In transit", "open"],
  UD: ["Undelivered", "critical"],
  RT: ["RTO", "critical"],
  RL: ["Redirected", "open"],
  NF: ["No info yet", "muted"],
};

export function bluedartStatusLabel(statusType) {
  return (BLUEDART_STATUS_META[statusType] || [statusType || "Not tracked", "muted"])[0];
}

export function bluedartStatusClass(statusType) {
  return (BLUEDART_STATUS_META[statusType] || [null, "muted"])[1];
}

// DTDC's shipment_tracking.status_type -- unlike Bluedart, DTDC gives no
// short code at the shipment-header level, just a free-text status
// (trackHeader.strStatus, e.g. "Delivered") -- so this keys on that text
// directly (case-insensitive) rather than a fixed code. Only "Delivered"
// is confirmed so far (see scripts/sync_dtdc_tracking.py) -- add other
// known values here as they're observed; anything unmapped still shows
// its own raw text via the fallback, so nothing silently disappears.
export const DTDC_STATUS_META = {
  delivered: ["Delivered", "good"],
  "in transit": ["In transit", "open"],
  "out for delivery": ["Out for delivery", "open"],
  "pickup awaited": ["Pickup awaited", "muted"],
};

export function dtdcStatusLabel(statusType) {
  return (DTDC_STATUS_META[(statusType || "").toLowerCase()] || [statusType || "Not tracked", "muted"])[0];
}

export function dtdcStatusClass(statusType) {
  return (DTDC_STATUS_META[(statusType || "").toLowerCase()] || [null, "muted"])[1];
}

// Lets Transport (tracked via Softpal's TrackingApiCommon_Softpal API)'s
// shipment_tracking.status_type -- same reasoning as DTDC_STATUS_META
// above: Softpal DOES return a short status_code at the header level
// (e.g. "OFD"), but only 2 codes have been observed so far and historical
// scan entries don't carry one at all, so this keys on the free-text
// current_status_name instead, for full and consistent coverage. Only
// these 5 are confirmed so far (see scripts/sync_lets_transport_tracking.py)
// -- add other known values here as they're observed.
export const LETS_TRANSPORT_STATUS_META = {
  "shipment booked": ["Booked", "muted"],
  "in transit": ["In transit", "open"],
  "arrived hub": ["Arrived hub", "open"],
  "out for delivery": ["Out for delivery", "open"],
  delivered: ["Delivered", "good"],
};

export function letsTransportStatusLabel(statusType) {
  return (LETS_TRANSPORT_STATUS_META[(statusType || "").toLowerCase()] || [statusType || "Not tracked", "muted"])[0];
}

export function letsTransportStatusClass(statusType) {
  return (LETS_TRANSPORT_STATUS_META[(statusType || "").toLowerCase()] || [null, "muted"])[1];
}

// Payment status on a po_invoice_uploads row -- same [label, chip color]
// pattern as STATUS_META above. Deliberately only two real states: an
// invoice has either been settled or it hasn't, and nothing in between is
// worth showing a vendor.
//
// null/undefined is NOT the same as "pending" and must keep its own label:
// it means no payment record has synced for this invoice yet, whereas
// "pending" is a positive statement that the source says it's unpaid.
// Until the sync that populates this column exists, every row is null and
// the dashboard reads exactly as it did before -- no column is quietly
// asserting an unpaid status nobody actually confirmed.
export const PAYMENT_STATUS_META = {
  pending: ["Pending", "open"],
  paid: ["Paid", "good"],
};

export function paymentStatusLabel(status) {
  if (!status) return "Pending integration";
  return (PAYMENT_STATUS_META[status] || [status, "muted"])[0];
}

export function paymentStatusClass(status) {
  if (!status) return "muted";
  return (PAYMENT_STATUS_META[status] || [null, "muted"])[1];
}

// poCodesWithGrn is optional (existing callers that don't care about the
// COMPLETE-with-no-GRN rule can omit it and get the old REJECTED/CREATED-
// only behavior).
export function visiblePos(pos, poCodesWithGrn) {
  return pos.filter(p => {
    if (HIDDEN_STATUSES.has(p.status)) return false;
    // Uniware marking a PO COMPLETE while it never had a single GRN raised
    // against it isn't a real fulfilled order -- it reads as a cancelled
    // invoice, so treat it the same as REJECTED/CREATED above and hide it
    // everywhere (Kalrav's explicit call).
    if (p.status === "COMPLETE" && poCodesWithGrn && !poCodesWithGrn.has(p.po_code)) return false;
    return true;
  });
}

// Uniware can carry the same invoice number on more than one GRN record
// (e.g. a vendor's document referenced across separate goods-receipt
// entries) -- a plain `new Set` only catches exact string matches, so
// whitespace or casing differences between those records ("LMF-4321 " vs
// "LMF-4321") still showed the same invoice twice. Trims + compares
// case-insensitively, but keeps the first occurrence's original casing
// for display.
export function dedupeInvoiceNumbers(numbers) {
  const seen = new Map(); // normalized key -> original (trimmed) value to display
  for (const raw of numbers) {
    if (!raw) continue;
    const trimmed = String(raw).trim();
    if (!trimmed) continue;
    const key = trimmed.toUpperCase();
    if (!seen.has(key)) seen.set(key, trimmed);
  }
  return [...seen.values()];
}

// Open/approved POs before completed ones; within each of those two
// groups, largest PO value first.
export function poSortComparator(a, b) {
  const aGroup = TERMINAL_STATUSES.has(a.status) ? 1 : 0;
  const bGroup = TERMINAL_STATUSES.has(b.status) ? 1 : 0;
  if (aGroup !== bGroup) return aGroup - bGroup;
  return (Number(b.total_amount) || 0) - (Number(a.total_amount) || 0);
}
