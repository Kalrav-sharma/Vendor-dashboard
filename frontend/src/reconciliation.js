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
// Same for every role -- vendors see exactly the same reconciliation
// status/reason as internal staff (Kalrav's explicit call).
import { isPoCodeTypo } from "./format.js";

export function reconciliationLabel(row) {
  const status = row.match_status;
  if (status === "pending") return { text: "Checking…", cls: "muted" };
  if (status === "error") return { text: "Check failed", cls: "critical" };
  // needs_review is set only when no GRN at all has been raised against
  // this PO yet (see check-invoice-match/index.ts) -- "GRN Pending" says
  // exactly that, rather than the vaguer "Needs review".
  if (status === "needs_review") return { text: "GRN Pending", cls: "open" };
  if (status === "matched") return { text: "Reconciliation passed", cls: "good" };
  if (status === "wrong_po") return { text: "Wrong PO", cls: "critical" };

  // status === "mismatch" -- pick the single most relevant reason. Real
  // rows very rarely trigger more than one of these at once; when they
  // do, this priority order picks the one most useful to act on first.
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

// The full, plain-language breakdown behind reconciliationLabel() -- for the "Why?" box on
// the Payments page and the credit-note upload window, so a vendor can see exactly which
// check failed, with the numbers, and what to do. Built only from what check-invoice-match
// recorded on the row (match_details), never re-derived.
//   checks:  [{ label, state: "ok" | "fail" | "warn" | "na", detail }]
//   actions: [string] -- what to do, most important first
const num = (v) => (v == null || v === "" || isNaN(Number(v)) ? null : Number(v));
const inr = (v) => (v == null ? "–" : "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 }));
const units = (v) => `${Number(v).toLocaleString("en-IN")} unit${Number(v) === 1 ? "" : "s"}`;

export function invoiceCheckBreakdown(row) {
  const d = row.match_details || {};
  const ex = d.extracted || {};
  // Uploaded on the wrong PO: nothing else about it means anything against this PO's GRNs.
  if (row.match_status === "wrong_po") {
    const other = ex.po_number_on_invoice || "another PO";
    if (isPoCodeTypo(row)) {
      return {
        checks: [{ label: "PO number on the invoice", state: "fail",
          detail: `The invoice quotes PO ${other}, but this order is ${row.po_code}. The PO number on the invoice must match exactly, so it isn't accepted.` }],
        actions: [`Upload the invoice again with the exact PO number ${row.po_code} on it.`],
      };
    }
    return {
      checks: [{ label: "PO number on the invoice", state: "fail",
        detail: `This invoice is for PO ${other}, but it was uploaded on ${row.po_code}. It isn't accepted on this PO.` }],
      actions: [
        `Upload the correct invoice for ${row.po_code} on this PO.`,
        `If invoice ${ex.invoice_number || ""} is for ${other}, make sure it's uploaded on that PO instead.`.replace("  ", " "),
      ],
    };
  }
  const disc = d.discrepancies || [];
  const byType = (t) => disc.find((x) => x.type === t);
  const invNo = ex.invoice_number || "this invoice";
  const grnFound = (d.grn_codes || []).length > 0;
  const noGrnYet = row.match_status === "needs_review";
  const checks = [];
  const actions = [];

  // 1. Invoice number recorded on the GRN
  const numberMiss = byType("invoice_number_no_grn_match");
  if (numberMiss) {
    const found = ((numberMiss.detail.match(/\(found: (.*)\)\.?$/) || [])[1] || "").split(",").map((x) => x.trim()).filter(Boolean).join(", ");
    checks.push({ label: "Invoice number on the warehouse GRN", state: "fail",
      detail: `Your invoice is ${invNo}, but the warehouse recorded ${found ? `invoice ${found}` : "a different invoice number"} when receiving goods on this PO.` });
    actions.push("Check the invoice number. If you uploaded the wrong invoice, upload the correct one on this PO. If this invoice is right, raise a ticket so our team can correct the number on the GRN.");
  } else if (noGrnYet) {
    checks.push({ label: "Invoice number on the warehouse GRN", state: "na", detail: "The warehouse hasn't recorded a GRN for this invoice yet, so it can't be matched. Nothing to do -- it's re-checked once the goods on it are received." });
  } else if (grnFound) {
    checks.push({ label: "Invoice number on the warehouse GRN", state: "ok", detail: `${invNo} matches GRN ${d.grn_codes.join(", ")}.` });
  }

  // 2. PO number printed on the invoice
  const poMiss = byType("po_number_mismatch");
  if (poMiss) {
    checks.push({ label: "PO number on the invoice", state: "fail",
      detail: `Your invoice mentions PO ${ex.po_number_on_invoice || "(unreadable)"}, but this order is ${row.po_code}.` });
    actions.push(`Make sure the invoice quotes the exact PO number, ${row.po_code}. If this invoice is for this order, raise a ticket and our team will review it.`);
  } else if (byType("missing_po_number")) {
    checks.push({ label: "PO number on the invoice", state: "warn", detail: "No PO number could be found on the invoice. Please quote the PO number on your invoices." });
  } else {
    checks.push({ label: "PO number on the invoice", state: "ok", detail: `Invoice quotes ${ex.po_number_on_invoice || row.po_code}.` });
  }

  // 3/4. Quantity and value against what the warehouse received
  const qtyMiss = byType("grn_qty_mismatch");
  const invQty = (ex.line_quantities || []).reduce((s, q) => s + (num(q) >= 0 ? num(q) : 0), 0);
  if (qtyMiss) {
    const m = qtyMiss.detail.match(/Invoice qty (\d+(?:\.\d+)?) vs .* received qty (\d+(?:\.\d+)?)/);
    const iq = m ? Number(m[1]) : invQty, gq = m ? Number(m[2]) : null;
    checks.push({ label: "Quantity: invoiced vs received", state: "fail",
      detail: gq == null ? qtyMiss.detail : `Invoiced ${units(iq)}, but the warehouse received ${units(gq)}.` });
    if (gq != null && iq > gq) actions.push(`Upload a credit note for the ${units(iq - gq)} invoiced but not received.`);
    if (gq != null && gq > iq) actions.push(`The warehouse received ${units(gq - iq)} more than this invoice covers. If another invoice covers them, upload it on this PO; otherwise raise a ticket.`);
  } else if (numberMiss) {
    checks.push({ label: "Quantity and value vs received", state: "na", detail: "Can't be compared until the invoice number matches one recorded on a GRN." });
  } else if (grnFound) {
    checks.push({ label: "Quantity: invoiced vs received", state: "ok", detail: invQty ? `${units(invQty)} invoiced and received.` : "Matches what the warehouse received." });
  }
  const valMiss = byType("grn_value_mismatch");
  const iv = num(d.invoice_value), gv = num(d.grn_value);
  if (valMiss) {
    checks.push({ label: "Value: invoiced vs received", state: "fail",
      detail: iv != null && gv != null ? `Invoice total ${inr(iv)}, but the goods received are worth ${inr(gv)} at PO rates.` : valMiss.detail });
    if (!qtyMiss && iv != null && gv != null && iv > gv) actions.push(`The quantity matches but the invoice is ${inr(iv - gv)} more than the received goods at PO rates -- check the rates on the invoice, and upload a credit note for the difference.`);
  } else if (grnFound) {
    checks.push({ label: "Value: invoiced vs received", state: "ok", detail: iv != null ? `${inr(iv)} matches the received goods.` : "Matches the received goods." });
  }

  // 5. Within what was ordered
  const overQty = byType("qty_exceeds_po"), overVal = byType("value_exceeds_po");
  if (overQty || overVal) {
    const q = overQty?.detail.match(/Invoice qty (\d+(?:\.\d+)?) exceeds .* qty (\d+(?:\.\d+)?)/);
    const v = overVal?.detail.match(/Invoice total ₹(\d+(?:\.\d+)?) exceeds .* value ₹(\d+(?:\.\d+)?)/);
    const parts = [
      q ? `Invoiced ${units(q[1])}, but the PO is for ${units(q[2])}.` : overQty?.detail,
      v ? `Invoice total ${inr(v[1])} is above the PO value of ${inr(v[2])}.` : overVal?.detail,
    ].filter(Boolean);
    checks.push({ label: "Within the PO", state: "fail", detail: parts.join(" ") });
    if (!actions.some((a) => /credit note/i.test(a))) actions.push("The invoice is for more than this PO ordered. Upload a credit note for the excess, or raise a ticket if a separate PO covers it.");
  } else if (!noGrnYet) {
    checks.push({ label: "Within the PO", state: "ok", detail: "Not more than this PO ordered." });
  }

  if (byType("low_confidence")) checks.push({ label: "Readability", state: "warn", detail: "The invoice PDF was hard to read -- if any number above looks wrong, please upload a clearer copy." });
  return { checks, actions };
}
