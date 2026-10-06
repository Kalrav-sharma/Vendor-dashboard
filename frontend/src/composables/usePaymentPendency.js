// Health Card › 3. Payment Pendency -- shared normalisation of the Payment Dashboard's
// po_invoice_uploads rows (useInvoiceUploads' allUploads, loaded by AdminApp) so 3.1 and
// 3.2 can't drift on what "unpaid", "due" and "one invoice" mean.
//   unpaid   payment_status !== 'paid' (null = no payout record synced yet = still owed;
//            the payout-file sync is manual, so a stale sync overstates pendency)
//   due      match_details.invoice_due_date (estimated at invoice date + 45d when not
//            printed -- invoice_due_date_estimated)
//   one      the same (vendor, invoice number) uploaded twice is still one payable
//   stuck    3-way match (PO × GRN × invoice) not clean: mismatch, GRN not raised yet
//            (needs_review), or the check failed (error). 'pending' = check still running.

const DAY = 86400000;
export const STUCK = new Set(["mismatch", "needs_review", "error", "wrong_po"]);
export const RECON_LABEL = { matched: "Reconciled", mismatch: "Mismatch", needs_review: "GRN pending", pending: "Checking", error: "Check failed" };

const num = v => (v == null || isNaN(Number(v)) ? null : Number(v));

// -> [{ id, vendorCode, po, inv, due, estimated, amount, days, status }]
// days = days past due (0 = due today, negative = not due yet, null = no due date)
export function unpaidInvoices(rows) {
  const today = new Date(new Date().toDateString());
  const seen = new Set();
  const out = [];
  for (const r of rows) {
    if (r.payment_status === "paid") continue;
    const d = r.match_details || {};
    const inv = d.extracted?.invoice_number;
    const key = inv ? `${r.vendor_code}|${inv.trim().toLowerCase()}` : r.id;
    if (seen.has(key)) continue;
    seen.add(key);
    const due = d.invoice_due_date ? new Date(`${d.invoice_due_date}T00:00:00`) : null;
    const days = due && !isNaN(due) ? Math.round((today - due) / DAY) : null;
    out.push({
      id: r.id, vendorCode: r.vendor_code, po: r.po_code, inv: inv || "–",
      due: d.invoice_due_date || null, estimated: !!d.invoice_due_date_estimated,
      amount: num(d.invoice_value), days, status: r.match_status,
    });
  }
  return out;
}

// group a list by vendor -> [{ code, label, list }]
export function byVendor(list, vendorLabel) {
  const by = new Map();
  for (const x of list) {
    if (!by.has(x.vendorCode)) by.set(x.vendorCode, []);
    by.get(x.vendorCode).push(x);
  }
  return [...by].map(([code, l]) => ({ code, label: vendorLabel(code), list: l }));
}

export const sumAmount = list => list.reduce((s, x) => s + (x.amount || 0), 0);
