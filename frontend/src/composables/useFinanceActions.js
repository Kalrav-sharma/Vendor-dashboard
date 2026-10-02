// Finance › Action Required -- Finance's daily worklist, built on the same ledger as the
// Payment Dashboard (usePaymentSummary.js' paymentLedger: one entry per invoice, duplicate
// uploads collapsed, plus POs Finance booked with no portal invoice).
//
// Booking itself happens in Oracle; the portal only learns about it when the weekly payout
// file is synced (scripts/sync_payment_status_manual.py, run by hand). So "not booked" here
// means "not booked as of the last payout sync" -- the page shows that sync's age.
//
//   ready      not booked (neither the payout sync nor Finance's own "Mark as booked" has
//              said so), and either reconciled (PO × GRN × invoice matched) or a mismatch the
//              vendor has since covered with a credit note -- Finance can book it now.
//   booked     booked in Oracle per the payout file, OR Finance has marked it booked in the
//              portal and the payout file just hasn't confirmed it yet (source: "sync" |
//              "portal") -- either way, payout not made yet. What the next payment run owes.
//   attention  unpaid invoices the portal can't move on its own: the match check failed or
//              never finished, or no invoice number / value was read off the PDF (the payout
//              sync matches on invoice number, so those would never pick up a status).
//   waiting    not booked and blocked on someone else: a credit note from the vendor
//              (mismatch) or a GRN from the warehouse (GRN pending). For chasing, not booking.
import { reconciliationLabel } from "../reconciliation.js";

const DAY = 86400000;
const STALE_CHECK_MS = DAY; // a match check still "running" after a day has stalled

export function financeQueues(ledger) {
  const now = Date.now();
  const invoices = ledger.filter((e) => e.kind === "invoice");

  const ready = invoices
    .filter((e) => e.pay === "none" && !e.bookedAt && (e.recon === "matched" || (e.recon === "mismatch" && e.cnSubmitted)))
    .map((e) => {
      const basis = e.recon === "matched" ? "reconciled" : "cn";
      return { ...e, basis, readySince: (basis === "cn" ? e.cnAt : e.checkedAt) || e.receivedAt };
    });

  const booked = ledger
    .filter((e) => e.pay === "booked" || (e.kind === "invoice" && e.pay === "none" && e.bookedAt))
    .map((e) => ({ ...e, source: e.pay === "booked" ? "sync" : "portal" }));

  const attention = [];
  for (const e of invoices) {
    if (e.pay === "paid") continue;
    const issues = [];
    if (e.recon === "error") issues.push("Match check failed");
    if (e.recon === "pending" && e.receivedAt && now - new Date(e.receivedAt).getTime() > STALE_CHECK_MS) issues.push("Match check stalled");
    if (e.recon !== "pending" && e.recon !== "error") {
      if (e.inv === "–") issues.push("No invoice number read");
      if (e.amount == null) issues.push("No invoice value read");
    }
    if (e.copies > 1) issues.push(`Uploaded ${e.copies}×`);
    if (issues.length) attention.push({ ...e, issues });
  }

  const waiting = invoices
    .filter((e) => e.pay === "none" && ((e.recon === "mismatch" && !e.cnSubmitted) || e.recon === "needs_review"))
    .map((e) => ({
      ...e,
      waitingOn: e.recon === "needs_review" ? "Warehouse · GRN" : "Vendor · credit note",
      reason: reconciliationLabel(e.row).text,
      waitingSince: e.checkedAt || e.receivedAt,
    }));

  return { ready, booked, attention, waiting };
}

// Whole days since an ISO timestamp (null-safe).
export const daysSince = (iso) => (iso ? Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / DAY)) : null);

// Latest payout-file sync the portal has seen, across invoices and PO-only bookings.
export function lastPayoutSync(uploads, pos) {
  let max = null;
  for (const x of [...uploads, ...pos]) {
    if (x.payment_synced_at && (!max || x.payment_synced_at > max)) max = x.payment_synced_at;
  }
  return max;
}
