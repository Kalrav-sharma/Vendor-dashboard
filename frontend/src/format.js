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

// Lakh/crore shorthand for headline tiles ("₹4.52 L", "₹1.08 Cr") where the full
// figure is too wide -- tables keep fmtMoney's exact value.
export function fmtMoneyCompact(n) {
  if (n === null || n === undefined) return "–";
  const v = Number(n);
  const abs = Math.abs(v);
  if (abs >= 1e7) return `₹${(v / 1e7).toFixed(2)} Cr`;
  if (abs >= 1e5) return `₹${(v / 1e5).toFixed(2)} L`;
  return fmtMoney(v);
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

// Raise a Ticket -- same [label, chip color] pattern as STATUS_META above.
export const TICKET_CATEGORY_META = {
  po_issue: "PO issue",
  payment_issue: "Payment issue",
  dispatch_issue: "Dispatch issue",
  other: "Other",
  po_request: "PO request",
};
export const TICKET_STATUS_META = {
  open: ["Open", "open"],
  in_progress: ["In progress", "info"],
  resolved: ["Resolved", "good"],
};

export function ticketCategoryLabel(category) {
  return TICKET_CATEGORY_META[category] || category || "Other";
}

export function ticketStatusLabel(status) {
  return (TICKET_STATUS_META[status] || [status || "Open", "muted"])[0];
}

export function ticketStatusClass(status) {
  return (TICKET_STATUS_META[status] || [null, "muted"])[1];
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
// these 6 are confirmed so far (see scripts/sync_lets_transport_tracking.py)
// -- add other known values here as they're observed.
//
// "pod uploaded" (seen 2026-09-29, AWB 100032616) is a real terminal
// status Softpal uses that isn't literally "Delivered" -- shows the same
// "Delivered" label as a plain delivered status instead of surfacing the
// raw courier text (Kalrav's explicit call, 2026-09-29). Still keyed
// separately here since it's a distinct raw value; see trackingBucket()
// below and TERMINAL_STATUSES in sync_lets_transport_tracking.py for the
// other places it's treated the same as "delivered".
export const LETS_TRANSPORT_STATUS_META = {
  "shipment booked": ["Booked", "muted"],
  "in transit": ["In transit", "open"],
  "arrived hub": ["Arrived hub", "open"],
  "out for delivery": ["Out for delivery", "open"],
  delivered: ["Delivered", "good"],
  "pod uploaded": ["Delivered", "good"],
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
  pending: ["Booked, Pending", "open"],
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

// The Payment Dashboard's actual displayed status for a po_invoice_uploads
// row -- payment_status itself (from the payout-file sync, see
// scripts/sync_payment_status_manual.py) is the source of truth once it's
// set, but most invoices haven't reached a payout run yet, and a bare
// "Pending integration" for all of them isn't useful. Before payment_status
// exists, the reconciliation outcome (match_status, see reconciliation.js)
// fills in something more specific:
//   - Oracle has actually booked the invoice and confirms it's unpaid
//     (payment_status = 'pending') -- "Booked, Pending", distinct from the
//     cases below so "booked but unpaid" is never confused with "not even
//     assessed yet".
//   - reconciliation passed ('matched') -- the invoice just hasn't come up
//     for payment yet, not stuck on anything -- "Pending".
//   - reconciliation found a mismatch ('mismatch' -- Short/Excess GRN,
//     PO/invoice number mismatch, exceeds PO) -- Finance can't process this
//     until the vendor corrects it with a credit note, so the UI asks for
//     one (needsCreditNote) instead of showing a status word at all.
//   - GRN not yet raised ('needs_review') -- can't even be assessed yet,
//     just "–" -- there's genuinely nothing to report.
//   - reconciliation still running or failed ('pending'/'error') --
//     genuinely unknown either way, keep the original "Pending integration".
export function effectivePaymentStatus(row) {
  if (row.payment_status) {
    return { text: paymentStatusLabel(row.payment_status), cls: paymentStatusClass(row.payment_status), needsCreditNote: false };
  }
  // Vendor side only -- VendorApp.vue attaches vendor_request to an upload the team has asked
  // a credit note for (useVendorRequests.js); no other caller's rows ever carry it.
  if (row.vendor_request?.kind === "credit_note") {
    return { text: "Credit note requested", cls: "critical", needsCreditNote: true };
  }
  if (row.match_status === "matched") {
    return {
      text: "Pending", cls: "muted", needsCreditNote: false,
      title: "Reconciliation passed -- this invoice hasn't come up in a payout run yet.",
    };
  }
  if (row.match_status === "mismatch") {
    return { text: "Upload Credit note", cls: "critical", needsCreditNote: true };
  }
  if (row.match_status === "needs_review") {
    return {
      text: "–", cls: "muted", needsCreditNote: false,
      title: "GRN not yet raised -- payment can't be assessed until it is.",
    };
  }
  return {
    text: "Pending integration", cls: "muted", needsCreditNote: false,
    title: "No payment record has synced for this invoice yet.",
  };
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

// Buckets a shipped dispatch row's courier-specific status_type into
// "in_transit" | "delivered" | "exception" | null -- each courier has its
// own status vocabulary (Bluedart: short codes; DTDC and Lets Transport:
// free text), so this is the one place that needs to know all three,
// rather than spreading courier-specific checks across every KPI that
// needs to know "is this shipment still moving." Shared by
// DispatchPlanningTable.vue's own KPI tiles and VendorApp.vue's Dashboard.
export function trackingBucket(row) {
  const status = row.tracking?.status_type;
  if (row.courier === "bluedart") {
    if (status === "IT") return "in_transit";
    if (status === "DL") return "delivered";
    if (["UD", "RT"].includes(status)) return "exception";
  } else if (row.courier === "dtdc") {
    const s = (status || "").toLowerCase();
    if (["in transit", "out for delivery", "pickup awaited"].includes(s)) return "in_transit";
    if (s === "delivered") return "delivered";
  } else if (row.courier === "letstransport") {
    const s = (status || "").toLowerCase();
    if (["shipment booked", "in transit", "arrived hub", "out for delivery"].includes(s)) return "in_transit";
    if (s === "delivered" || s === "pod uploaded") return "delivered";
  }
  return null;
}

// Distinct vendor_code/vendor_name pairs out of any list of PO-like rows,
// sorted by label -- used by both AdminApp.vue's own vendor filter (from
// its already-fetched currentPos) and the admin-only "preview as vendor"
// picker (from a dedicated unfiltered fetch in VendorApp.vue, since that
// page's own currentPos is deliberately scoped to just the vendor being
// previewed). Kept here rather than duplicated so the two lists can never
// drift in how they dedupe/label a vendor.
export function dedupeVendorOptions(rows) {
  const byCode = new Map();
  for (const row of rows) {
    if (!row.vendor_code || byCode.has(row.vendor_code)) continue;
    byCode.set(row.vendor_code, { code: row.vendor_code, label: row.vendor_name || row.vendor_code });
  }
  return [...byCode.values()].sort((a, b) => a.label.localeCompare(b.label));
}

// Open/approved POs before completed ones; within each of those two
// groups, largest PO value first.
export function poSortComparator(a, b) {
  const aGroup = TERMINAL_STATUSES.has(a.status) ? 1 : 0;
  const bGroup = TERMINAL_STATUSES.has(b.status) ? 1 : 0;
  if (aGroup !== bGroup) return aGroup - bGroup;
  return (Number(b.total_amount) || 0) - (Number(a.total_amount) || 0);
}
