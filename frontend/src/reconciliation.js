// Turns a po_invoice_uploads row's match_status/match_details into a
// short, specific label for the Payment Dashboard -- "Reconciliation
// passed" instead of a generic "Matches PO/GRN", and the actual reason
// (Excess GRN, Short GRN, PO mismatch, Invoice number mismatch) instead
// of a generic "Mismatch found" -- derived from the discrepancy types
// and the invoice/GRN values check-invoice-match already recorded.
//
// Shared between PaymentDashboardTable.vue (display) and
// usePaymentFilters.js (the Reconciliation filter dropdown), so the
// label shown and the label matched against can never drift apart.
export function reconciliationLabel(row) {
  const status = row.match_status;
  if (status === "pending") return { text: "Checking…", cls: "muted" };
  if (status === "error") return { text: "Check failed", cls: "critical" };
  if (status === "needs_review") return { text: "Needs review", cls: "open" };
  if (status === "matched") return { text: "Reconciliation passed", cls: "good" };

  // status === "mismatch" -- pick the single most relevant reason. Real
  // rows very rarely trigger more than one of these at once; when they
  // do, this priority order picks the one most useful to act on first.
  const details = row.match_details || {};
  const types = new Set((details.discrepancies || []).map((d) => d.type));

  if (types.has("po_number_mismatch")) return { text: "PO mismatch", cls: "critical" };
  if (types.has("invoice_number_no_grn_match")) return { text: "Invoice number mismatch", cls: "critical" };
  if (types.has("grn_value_mismatch") || types.has("grn_qty_mismatch")) {
    const invoiceValue = details.invoice_value;
    const grnValue = details.grn_value;
    if (invoiceValue != null && grnValue != null) {
      return grnValue > invoiceValue ? { text: "Excess GRN", cls: "critical" } : { text: "Short GRN", cls: "critical" };
    }
    return { text: "GRN mismatch", cls: "critical" };
  }
  if (types.has("qty_exceeds_po") || types.has("value_exceeds_po")) return { text: "Exceeds PO", cls: "critical" };
  return { text: "Mismatch found", cls: "critical" };
}
