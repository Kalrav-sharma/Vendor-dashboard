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
  needs_review: ["Needs review", "open"],
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

// Invoice submission status, from Jarvis query 594877's ORACLE_STATUS --
// same [label, chip color] pattern as STATUS_META above.
//
// This is NOT a payment status and must never be labelled as one. Oracle
// is where UC's payables live, and ORACLE_STATUS says whether the invoice
// RECORD got pushed there -- which happens within hours of receipting,
// long before anyone is paid (~95% of all rows are 'pushed'). Calling
// that "Paid" would tell a vendor their money has gone out when it
// hasn't. What it DOES tell them is genuinely useful and wasn't visible
// anywhere before: whether their invoice actually made it into the system
// that pays them, and if it didn't, that someone needs to fix it.
//
// null means the sync hasn't matched this invoice yet -- distinct from
// every real status, and never dressed up as one.
export const ORACLE_STATUS_META = {
  pushed: ["In Oracle", "good"],
  not_attempted: ["Not submitted", "open"],
  failed: ["Submission failed", "critical"],
};

export function invoiceStatusLabel(status) {
  if (!status) return "Not synced";
  return (ORACLE_STATUS_META[status] || [status, "muted"])[0];
}

export function invoiceStatusClass(status) {
  if (!status) return "muted";
  return (ORACLE_STATUS_META[status] || [null, "muted"])[1];
}

export function visiblePos(pos) {
  return pos.filter(p => !HIDDEN_STATUSES.has(p.status));
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
