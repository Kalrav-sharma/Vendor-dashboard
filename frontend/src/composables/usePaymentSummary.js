// Finance › Payment Dashboard (executive summary, every vendor) and Vendor Payments (one
// vendor) -- one ledger both pages summarise, so the all-vendor and single-vendor numbers
// are computed the same way and always add up.
//
// Ledger entries come from two places:
//   invoice  a po_invoice_uploads row (allUploads). The same (vendor, invoice number)
//            uploaded twice is still one payable -- the newest copy wins, unless an older
//            copy is the one the payout file marked paid.
//   po       a PO Finance already booked/paid with no invoice uploaded through the portal
//            (AdminApp's posWithPaymentNoInvoice) -- valued at the PO's total_amount.
// POs with neither an invoice nor a payment record ("awaiting invoice") aren't payables
// yet, so they're passed alongside, never mixed into the ledger.
//
// Same rules as usePaymentPendency.js (Health Card › Payment Pendency):
//   unpaid   payment_status !== 'paid' (null = no payout record synced yet = still owed)
//   overdue  unpaid and due date before today (due today isn't overdue yet)
//   stuck    3-way match (PO × GRN × invoice) not clean: mismatch, GRN pending, check failed
import { STUCK } from "./usePaymentPendency.js";

const DAY = 86400000;
const num = (v) => (v == null || v === "" || isNaN(Number(v)) ? null : Number(v));
const payOf = (s) => (s === "paid" ? "paid" : s ? "booked" : "none");

// -> [{ key, kind, vendorCode, po, inv, amount, grnAmount, pay, paidAt, syncedAt, due, estimated,
//       days, recon, cnSubmitted, cnAt, checkedAt, receivedAt, copies, row, bookedAt, bookedBy }]
// days = days past due (0 = due today, negative = not due yet, null = no due date)
// copies = how many uploads collapsed into this one invoice; row = the kept upload row
// bookedAt/bookedBy = Finance's own "mark as booked" (finance_booked_*), separate from pay/
// paidAt above, which come only from the payout-file sync -- see useFinanceActions.js
// The key paymentLedger() collapses duplicate uploads of one invoice under -- exported so
// other per-upload data (e.g. GRN -> Finance handoffs) can be matched to a ledger entry.
export const ledgerKey = (r) => {
  const inv = r.match_details?.extracted?.invoice_number;
  return inv ? `${r.vendor_code}|${String(inv).trim().toLowerCase()}` : r.id;
};

export function paymentLedger(uploads, posWithPayment = []) {
  const today = new Date(new Date().toDateString());
  const byKey = new Map();
  const copies = new Map();
  for (const r of uploads) {
    const key = ledgerKey(r);
    copies.set(key, (copies.get(key) || 0) + 1);
    const prev = byKey.get(key);
    if (prev && !(prev.payment_status !== "paid" && r.payment_status === "paid")) continue;
    byKey.set(key, r);
  }

  const out = [];
  for (const [key, r] of byKey) {
    const d = r.match_details || {};
    const due = d.invoice_due_date ? new Date(`${d.invoice_due_date}T00:00:00`) : null;
    out.push({
      key, kind: "invoice", vendorCode: r.vendor_code, po: r.po_code,
      inv: d.extracted?.invoice_number || "–", amount: num(d.invoice_value), grnAmount: num(d.grn_value),
      pay: payOf(r.payment_status), paidAt: r.payment_date || null, syncedAt: r.payment_synced_at || null,
      due: d.invoice_due_date || null, estimated: !!d.invoice_due_date_estimated,
      days: due && !isNaN(due) ? Math.round((today - due) / DAY) : null,
      recon: r.match_status, cnSubmitted: !!r.credit_note_storage_path, cnAt: r.credit_note_uploaded_at || null,
      checkedAt: r.checked_at || null, receivedAt: r.created_at || null,
      copies: copies.get(key), row: r,
      bookedAt: r.finance_booked_at || null, bookedBy: r.finance_booked_by_name || null,
    });
  }
  for (const p of posWithPayment) {
    out.push({
      key: `po:${p.po_code}`, kind: "po", vendorCode: p.vendor_code, po: p.po_code,
      inv: "–", amount: num(p.total_amount), grnAmount: null,
      pay: payOf(p.payment_status), paidAt: p.payment_date || null, syncedAt: p.payment_synced_at || null,
      due: null, estimated: false, days: null, recon: null, cnSubmitted: false, cnAt: null,
      checkedAt: null, receivedAt: null, copies: 1, row: null, bookedAt: null, bookedBy: null,
    });
  }
  return out;
}

export const sumAmount = (list) => list.reduce((s, x) => s + (x.amount || 0), 0);

const isOverdue = (e) => e.days != null && e.days > 0;
const isDueSoon = (e) => e.days != null && e.days <= 0 && e.days >= -7; // today .. today+7

// Outstanding (unpaid) value by how far past its due date it is.
export const AGE_BUCKETS = [
  { key: "later", label: "Not due yet (8+ days)", cls: "", test: (e) => e.days != null && e.days < -7 },
  { key: "soon", label: "Due in next 7 days", cls: "", test: isDueSoon },
  { key: "d10", label: "1–10 days overdue", cls: "open", test: (e) => isOverdue(e) && e.days <= 10 },
  { key: "d10p", label: "10+ days overdue", cls: "critical", test: (e) => e.days > 10 },
  { key: "none", label: "No due date", cls: "muted", test: (e) => e.days == null },
];

// Outstanding (unpaid) value by where its 3-way match stands -- paid invoices are left out
// on purpose: a mismatch that was paid anyway isn't anything to act on any more.
export const RECON_BUCKETS = [
  { key: "matched", label: "Reconciled", cls: "good", test: (e) => e.recon === "matched" },
  { key: "grn", label: "GRN pending", cls: "open", test: (e) => e.recon === "needs_review" },
  { key: "cn_pending", label: "Mismatch · credit note pending", cls: "critical", test: (e) => e.recon === "mismatch" && !e.cnSubmitted },
  { key: "cn_done", label: "Mismatch · credit note submitted", cls: "open", test: (e) => e.recon === "mismatch" && e.cnSubmitted },
  { key: "error", label: "Check failed", cls: "critical", test: (e) => e.recon === "error" },
  { key: "running", label: "Check running", cls: "muted", test: (e) => e.kind === "invoice" && (e.recon === "pending" || !e.recon) },
  { key: "no_invoice", label: "Booked without portal invoice", cls: "muted", test: (e) => e.kind === "po" },
];

// Every payable by where it stands in Finance's payout run.
export const PAY_BUCKETS = [
  { key: "paid", label: "Paid", cls: "good", test: (e) => e.pay === "paid" },
  { key: "booked", label: "Booked, payout pending", cls: "open", test: (e) => e.pay === "booked" },
  { key: "none", label: "Not booked yet", cls: "muted", test: (e) => e.pay === "none" },
];

const bucketise = (buckets, list) => buckets.map((b) => {
  const items = list.filter(b.test);
  return { key: b.key, label: b.label, cls: b.cls, list: items, count: items.length, amount: sumAmount(items) };
});

// Invoices received (by upload date) vs paid (by payment date), one row per month since
// August 1 of the current year -- same fixed-window convention as AdminApp.vue's
// paymentWindowStart / VendorApp.vue's dashCompleteWindowStart, so this never shows a month
// the rest of the payment view doesn't otherwise count data from.
function monthlyFlow(entries) {
  const now = new Date();
  const augStart = new Date(now.getFullYear(), 7, 1);
  const months = Math.max(1, (now.getFullYear() - augStart.getFullYear()) * 12 + (now.getMonth() - augStart.getMonth()) + 1);
  const rows = [];
  for (let i = months - 1; i >= 0; i--) {
    const start = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const end = new Date(now.getFullYear(), now.getMonth() - i + 1, 1);
    const inMonth = (iso) => { if (!iso) return false; const t = new Date(iso); return t >= start && t < end; };
    const received = entries.filter((e) => e.kind === "invoice" && inMonth(e.receivedAt));
    const paid = entries.filter((e) => e.pay === "paid" && inMonth(e.paidAt));
    rows.push({
      key: `${start.getFullYear()}-${start.getMonth()}`,
      label: start.toLocaleDateString("en-IN", { month: "short", year: "numeric" }),
      received, paid, receivedAmount: sumAmount(received), paidAmount: sumAmount(paid),
    });
  }
  return rows;
}

// entries: a paymentLedger() (already scoped to one vendor, or not); awaiting: the matching
// "no invoice, no payment record" POs.
export function summarise(entries, awaiting = []) {
  const unpaid = entries.filter((e) => e.pay !== "paid");
  const paid = entries.filter((e) => e.pay === "paid");
  const overdue = unpaid.filter(isOverdue);
  const timed = paid.filter((e) => e.kind === "invoice" && e.paidAt && e.receivedAt);
  const daysToPay = timed.length
    ? Math.round(timed.reduce((s, e) => s + (new Date(e.paidAt) - new Date(e.receivedAt)) / DAY, 0) / timed.length)
    : null;
  return {
    entries, unpaid, paid, overdue,
    dueSoon: unpaid.filter(isDueSoon),
    stuck: unpaid.filter((e) => STUCK.has(e.recon)),
    awaiting, awaitingAmount: awaiting.reduce((s, p) => s + (num(p.total_amount) || 0), 0),
    invoiced: sumAmount(entries), paidAmount: sumAmount(paid), unpaidAmount: sumAmount(unpaid),
    oldestOverdue: overdue.reduce((m, e) => Math.max(m, e.days), 0),
    daysToPay, daysToPaySample: timed.length,
    noValue: entries.filter((e) => e.amount == null).length,
    vendorCount: new Set([...entries.map((e) => e.vendorCode), ...awaiting.map((p) => p.vendor_code)]).size,
    ageing: bucketise(AGE_BUCKETS, unpaid),
    recon: bucketise(RECON_BUCKETS, unpaid),
    payment: bucketise(PAY_BUCKETS, entries),
    monthly: monthlyFlow(entries),
  };
}

// One summarise() per vendor, biggest outstanding first.
export function vendorRollup(entries, awaiting, vendorLabel) {
  const codes = new Set([...entries.map((e) => e.vendorCode), ...awaiting.map((p) => p.vendor_code)]);
  return [...codes]
    .map((code) => ({
      code, label: vendorLabel(code),
      s: summarise(entries.filter((e) => e.vendorCode === code), awaiting.filter((p) => p.vendor_code === code)),
    }))
    .sort((a, b) => b.s.unpaidAmount - a.s.unpaidAmount || sumAmount(b.s.overdue) - sumAmount(a.s.overdue) || a.label.localeCompare(b.label));
}
