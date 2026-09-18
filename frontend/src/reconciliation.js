// Turns a po_invoice_uploads row's match_status/match_details into a
// short, specific label for the Payment Dashboard -- "Reconciliation
// passed" instead of a generic "Matches PO/GRN", and the actual reason
// (Excess GRN, Short GRN, PO mismatch, Invoice number mismatch) instead
// of a generic "Mismatch found" -- derived from the discrepancy types
// and the invoice/GRN values check-invoice-match already recorded.
//
// isInternalStaff gates that specificity (Kalrav's explicit call: a
// vendor shouldn't see exactly WHY/BY HOW MUCH their invoice didn't
// reconcile, only whether it did) -- defaults to false, so a caller must
// opt in explicitly rather than accidentally leaking it. false collapses
// straight to "Matches"/"Doesn't match" for the two conclusive outcomes;
// the in-progress/no-GRN-yet/error states already say nothing about a
// discrepancy's specifics, so those stay the same either way.
//
// Shared between PaymentDashboardTable.vue (display) and
// usePaymentFilters.js (the Reconciliation filter dropdown), so the
// label shown and the label matched against can never drift apart.
export function reconciliationLabel(row, isInternalStaff = false) {
  const status = row.match_status;
  if (status === "pending") return { text: "Checking…", cls: "muted" };
  if (status === "error") return { text: "Check failed", cls: "critical" };
  if (status === "needs_review") return { text: "Needs review", cls: "open" };
  if (status === "matched") return { text: isInternalStaff ? "Reconciliation passed" : "Matches", cls: "good" };

  // status === "mismatch"
  if (!isInternalStaff) return { text: "Doesn't match", cls: "critical" };

  // Internal staff: pick the single most relevant reason. Real rows very
  // rarely trigger more than one of these at once; when they do, this
  // priority order picks the one most useful to act on first.
  const details = row.match_details || {};
  const types = new Set((details.discrepancies || []).map((d) => d.type));

  if (types.has("po_number_mismatch")) return { text: "PO mismatch", cls: "critical" };
  if (types.has("invoice_number_no_grn_match")) return { text: "Invoice number mismatch", cls: "critical" };
  // Checked BEFORE the GRN mismatch below on purpose: an invoice that
  // exceeds what was ever ordered on the PO is the more fundamental
  // problem -- GRN received qty can never exceed the PO's ordered qty,
  // so this case would otherwise always also trip grn_qty_mismatch and
  // get mislabeled "Short GRN", masking the real issue.
  if (types.has("qty_exceeds_po") || types.has("value_exceeds_po")) return { text: "Exceeds PO", cls: "critical" };
  if (types.has("grn_value_mismatch") || types.has("grn_qty_mismatch")) {
    const invoiceValue = details.invoice_value;
    const grnValue = details.grn_value;
    if (invoiceValue != null && grnValue != null) {
      return grnValue > invoiceValue ? { text: "Excess GRN", cls: "critical" } : { text: "Short GRN", cls: "critical" };
    }
    return { text: "GRN mismatch", cls: "critical" };
  }
  return { text: "Mismatch found", cls: "critical" };
}
