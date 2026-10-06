<script setup>
import { computed, ref, watch } from "vue";
import { fmtMoney, fmtDateOnly, paymentStatusLabel, paymentStatusClass } from "../format.js";
import ReconciliationChip from "./ReconciliationChip.vue";
import InvoiceCheckExplainer from "./InvoiceCheckExplainer.vue";
import PaymentStatusChip from "./PaymentStatusChip.vue";
import ViewInvoiceButton from "./ViewInvoiceButton.vue";
import InvoiceUploadButton from "./InvoiceUploadButton.vue";
import InvoiceUploadModal from "./InvoiceUploadModal.vue";
import SummaryKpis from "./SummaryKpis.vue";

const props = defineProps({
  rows: { type: Array, required: true },        // already filtered
  filters: { type: Object, required: true },     // reactive filter state, mutated directly (v-model)
  reconciliationOptions: { type: Array, required: true }, // distinct reconciliation labels present in the data
  paymentStatusOptions: { type: Array, default: () => [] }, // distinct payment status labels present in the data
  vendorOptions: { type: Array, default: null }, // [{code, label}] -- null hides the Vendor column entirely
  vendorLabel: { type: Function, default: null }, // (code) => string -- required when vendorOptions is set
  onOpenPo: { type: Function, required: true }, // (poCode) => void
  uploaderLabel: { type: String, default: "" }, // current user's display name, recorded on an uploaded credit note
  showVendorKpis: { type: Boolean, default: false }, // vendor.html's own 4-tile set below; admin.html keeps the original tiles
  showKpis: { type: Boolean, default: true }, // false where the page already shows its own tiles above (admin Vendor Payments)
  showBuckets: { type: Boolean, default: false }, // vendor.html's All/CN Required/Invoice Copy Needed/No Action Needed/Paid tabs
  posNeedingInvoice: { type: Array, default: () => [] }, // POs with no invoice uploaded yet at all -- vendor-only, see posNeedingInvoice in VendorApp.vue
  posWithPayment: { type: Array, default: () => [] }, // POs with no invoice uploaded but Finance already has a payment record for them (posWithPaymentNoInvoice in VendorApp.vue) -- vendor-only
  // vendor.html only -- { bucket } set by a Dashboard shortcut ("5 POs need
  // an invoice") to land on that bucket; a fresh object each time, so
  // re-clicking the same shortcut re-applies it after a manual tab change.
  focusBucket: { type: Object, default: null },
});

function invoiceNumber(row) { return row.match_details?.extracted?.invoice_number || "–"; }
function invoiceValue(row) { return row.match_details?.invoice_value ?? null; }
function grnValue(row) { return row.match_details?.grn_value ?? null; }
function dueDate(row) { return row.match_details?.invoice_due_date || null; }
function dueDateEstimated(row) { return !!row.match_details?.invoice_due_date_estimated; }

// Date-only comparison -- an invoice due today isn't overdue yet. An
// already-paid invoice is never overdue no matter what its due date says.
const todayStart = new Date(new Date().toDateString());
function isOverdue(row) {
  const due = dueDate(row);
  if (row.payment_status === "paid") return false;
  return !!due && new Date(due) < todayStart && row.match_status !== "matched";
}

// "Pending" here means "not yet settled": every row whose payment_status
// isn't 'paid'. That deliberately counts rows with no payment record synced
// yet (payment_status null) as pending too -- an invoice nobody has
// confirmed paid is still outstanding from the vendor's point of view.
// Before the payment sync exists, every row is null and this tile reads
// exactly as it did when it was a plain row count.
function isPaid(row) { return row.payment_status === "paid"; }

// "Why?" next to a flagged reconciliation chip -- one shared explanation box for the table.
const explainRow = ref(null);

// One shared upload popup for the "Invoice Copy Needed" bucket's rows --
// same pattern as PoTrackingTable's per-row upload button.
const uploadModalPoCode = ref(null);
const uploadModalVendorCode = ref(null);
function openUploadModal(po) {
  uploadModalPoCode.value = po.po_code;
  uploadModalVendorCode.value = po.vendor_code;
}

const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000;

// Vendor's own 4-tile set -- amount + backing invoice count for the three
// money tiles; "Action required" has no natural currency total, so its
// value is just the combined action-item count written out as "N invoices"
// (no separate sublabel repeating the same number).
// "To be paid this week" = booked on the payout file but not yet paid
// (payment_status "pending" -- see PAYMENT_STATUS_META's "Booked, Pending")
// with a real, non-zero invoice value. "Action required" = a PO still
// missing its invoice copy AND with no payout record either
// (posNeedingInvoice, passed in from VendorApp.vue since that's PO-level
// data this table doesn't otherwise have) OR an uploaded invoice flagged
// for a credit note (match_status "mismatch") -- an already-paid invoice
// can't be in either group.
//
// posWithPayment (also PO-level, from VendorApp.vue) folds in here too --
// a PO with no uploaded invoice but a real Finance payment record is just
// as much a "payment made" or "to be paid" as an uploaded one, using the
// PO's own total_amount in place of an invoice's extracted value.
const vendorKpiTiles = computed(() => {
  const paidRows = props.rows.filter(isPaid);
  const paidPos = props.posWithPayment.filter((p) => p.payment_status === "paid");
  const madeTillDate = paidRows.reduce((s, r) => s + (invoiceValue(r) || 0), 0)
    + paidPos.reduce((s, p) => s + (Number(p.total_amount) || 0), 0);

  const weekCutoff = Date.now() - SEVEN_DAYS_MS;
  const paidLastWeekRows = paidRows.filter(r => r.payment_date && new Date(r.payment_date).getTime() >= weekCutoff);
  const paidLastWeekPos = paidPos.filter(p => p.payment_date && new Date(p.payment_date).getTime() >= weekCutoff);
  const paidLastWeek = paidLastWeekRows.reduce((s, r) => s + (invoiceValue(r) || 0), 0)
    + paidLastWeekPos.reduce((s, p) => s + (Number(p.total_amount) || 0), 0);

  const toBePaidRows = props.rows.filter(r => r.payment_status === "pending" && (invoiceValue(r) || 0) > 0);
  const toBePaidPos = props.posWithPayment.filter(p => p.payment_status === "pending" && (Number(p.total_amount) || 0) > 0);
  const toBePaid = toBePaidRows.reduce((s, r) => s + (invoiceValue(r) || 0), 0)
    + toBePaidPos.reduce((s, p) => s + (Number(p.total_amount) || 0), 0);

  const creditNoteNeededCount = props.rows.filter(r => !isPaid(r) && r.match_status === "mismatch").length;
  const actionRequired = props.posNeedingInvoice.length + creditNoteNeededCount;

  return [
    {
      label: "Payments made till date", value: fmtMoney(madeTillDate),
      sublabel: `${paidRows.length + paidPos.length} invoices`,
    },
    {
      label: "Paid last week", value: fmtMoney(paidLastWeek),
      sublabel: `${paidLastWeekRows.length + paidLastWeekPos.length} invoices`,
    },
    {
      label: "To be paid this week", value: fmtMoney(toBePaid),
      sublabel: `${toBePaidRows.length + toBePaidPos.length} invoices`,
    },
    { label: "Action required", value: `${actionRequired} invoices`, cls: actionRequired > 0 ? "critical" : "" },
  ];
});

const adminKpiTiles = computed(() => {
  const pending = props.rows.filter(r => !isPaid(r)).length;
  const onTrack = props.rows.filter(r => r.match_status === "matched" && !isOverdue(r)).length;
  const hasIssues = props.rows.filter(r => !isPaid(r) && (r.match_status === "mismatch" || r.match_status === "error")).length;
  const overdue = props.rows.filter(isOverdue).length;
  return [
    { label: "Total invoices pending", value: pending },
    { label: "No issues -- on track", value: onTrack, cls: "good" },
    { label: "Has issues", value: hasIssues, cls: hasIssues > 0 ? "critical" : "" },
    { label: "Overdue", value: overdue, cls: overdue > 0 ? "critical" : "" },
  ];
});

const kpiTiles = computed(() => (props.showVendorKpis ? vendorKpiTiles.value : adminKpiTiles.value));

// Segregates the same rows the KPI tiles above already count into
// clickable buckets -- "All" is every real invoice row PLUS both kinds of
// synthetic PO-only entries below, so its count matches the sum of the
// other four exactly (mirrors why Action Required above adds different-
// shaped things together). No-Action-Needed is deliberately the leftover
// bucket: not yet paid and not flagged for a credit note -- covers
// pending/matched/needs_review/error alike, since none of those need
// anything from the vendor beyond waiting -- PLUS any posWithPayment PO
// still payment_status "pending" (booked but not yet paid, same "nothing
// to do but wait" logic, just with no invoice ever uploaded for it).
const BUCKETS = [
  { key: "all", label: "All" },
  { key: "requests", label: "Requests" }, // shown only while there are any -- see visibleBuckets
  { key: "wrong_po", label: "Wrong PO" },   // same -- invoices uploaded on a PO they don't belong to
  { key: "cn_required", label: "CN Required" },
  { key: "invoice_copy_needed", label: "Invoice Copy Needed" },
  { key: "no_action_needed", label: "No Action Needed" },
  { key: "paid", label: "Paid" },
];
// Requests: invoices the team has asked a credit note / corrected invoice on (vendor side
// only -- rows carry vendor_request, see VendorApp.vue). Their own bucket, so they're out of
// CN Required / No Action Needed and every row still lands in exactly one bucket.
const requestRows = computed(() => props.rows.filter((r) => r.vendor_request));
// Paid wins: an invoice paid despite a mismatch (e.g. PBRU/PO2627/0326's LMF-2627/5095, paid
// 2026-10-03) needs nothing more from the vendor, so it sits in Paid only, never in CN Required too.
const cnRequiredRows = computed(() => props.rows.filter((r) => !isPaid(r) && r.match_status === "mismatch" && !r.vendor_request));
const paidRows = computed(() => props.rows.filter(isPaid));
const wrongPoRows = computed(() => props.rows.filter((r) => !isPaid(r) && r.match_status === "wrong_po" && !r.vendor_request));
const noActionRows = computed(() => props.rows.filter((r) => !isPaid(r) && r.match_status !== "mismatch" && r.match_status !== "wrong_po" && !r.vendor_request));
// posWithPayment split by its own payment_status -- "paid" joins the Paid
// bucket outright, anything else (in practice just "pending") joins
// No Action Needed alongside the uploaded-but-unpaid invoices above.
const posPaymentPaid = computed(() => props.posWithPayment.filter((p) => p.payment_status === "paid"));
const posPaymentPending = computed(() => props.posWithPayment.filter((p) => p.payment_status !== "paid"));
const activeBucket = ref("all");
watch(() => props.focusBucket, (f) => { if (f?.bucket) activeBucket.value = f.bucket; });
const bucketCounts = computed(() => ({
  all: props.rows.length + props.posNeedingInvoice.length + props.posWithPayment.length,
  requests: requestRows.value.length,
  wrong_po: wrongPoRows.value.length,
  cn_required: cnRequiredRows.value.length,
  invoice_copy_needed: props.posNeedingInvoice.length,
  no_action_needed: noActionRows.value.length + posPaymentPending.value.length,
  paid: paidRows.value.length + posPaymentPaid.value.length,
}));

// Real invoice rows and PO-only entries have different shapes, so each
// display row is tagged with its kind and the template branches per cell.
// need_invoice = no upload, no payment record at all (genuine action
// needed). po_payment = no upload, but Finance already has a payment
// record for it (posWithPayment) -- nothing needed from the vendor.
function invoiceEntry(row) { return { kind: "invoice", row }; }
function needInvoiceEntry(po) { return { kind: "need_invoice", po }; }
function poPaymentEntry(po) { return { kind: "po_payment", po }; }
// The full merged set -- real invoice rows plus the two kinds of PO-only entries
// (no invoice uploaded at all; Finance has a payment record but no invoice was
// uploaded through the portal). Used both as the "All" bucket and, when the caller
// doesn't want bucket tabs at all (admin.html), as the only list shown -- so those
// PO-only entries are visible there too, not just on a vendor's own dashboard.
const allEntries = computed(() => [
  ...props.rows.map(invoiceEntry),
  ...props.posNeedingInvoice.map(needInvoiceEntry),
  ...props.posWithPayment.map(poPaymentEntry),
]);
const visibleBuckets = computed(() =>
  BUCKETS.filter((b) => (b.key !== "requests" || requestRows.value.length || activeBucket.value === "requests")
    && (b.key !== "wrong_po" || wrongPoRows.value.length || activeBucket.value === "wrong_po")));
const displayRows = computed(() => {
  if (!props.showBuckets) return allEntries.value;
  switch (activeBucket.value) {
    case "requests": return requestRows.value.map(invoiceEntry);
    case "wrong_po": return wrongPoRows.value.map(invoiceEntry);
    case "cn_required": return cnRequiredRows.value.map(invoiceEntry);
    case "invoice_copy_needed": return props.posNeedingInvoice.map(needInvoiceEntry);
    case "no_action_needed":
      return [...noActionRows.value.map(invoiceEntry), ...posPaymentPending.value.map(poPaymentEntry)];
    case "paid":
      return [...paidRows.value.map(invoiceEntry), ...posPaymentPaid.value.map(poPaymentEntry)];
    default:
      return allEntries.value;
  }
});
</script>

<template>
  <SummaryKpis v-if="showKpis" :tiles="kpiTiles" />

  <div v-if="showBuckets" class="bucket-tabs">
    <button
      v-for="b in visibleBuckets" :key="b.key" type="button"
      class="bucket-tab" :class="{ active: activeBucket === b.key, 'vr-tab': b.key === 'requests' || b.key === 'wrong_po' }"
      @click="activeBucket = b.key"
    >
      {{ b.label }} <span class="bucket-count">{{ bucketCounts[b.key] }}</span>
    </button>
  </div>

  <div class="field" style="max-width: 340px; margin-bottom: 14px;">
    <label for="payment-top-search">Search{{ vendorOptions ? " vendor," : "" }} PO code, invoice number…</label>
    <input id="payment-top-search" v-model="filters.search" type="text" placeholder="Type to search…">
  </div>

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th v-if="vendorOptions">Vendor</th>
          <th>PO code</th><th>Invoice number</th><th class="col-tight">Invoice copy</th>
          <th class="num">Invoice value</th><th class="num">GRN value</th>
          <th>Due date</th><th>Reconciliation</th><th>Payment status</th>
        </tr>
        <tr class="filter-row">
          <td v-if="vendorOptions">
            <select v-model="filters.vendor">
              <option value="">All</option>
              <option v-for="v in vendorOptions" :key="v.code" :value="v.code">{{ v.label }}</option>
            </select>
          </td>
          <td><input v-model="filters.poCode" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.invoiceNumber" type="text" placeholder="Filter…"></td>
          <td></td>
          <td><input v-model="filters.invoiceValue" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.grnValue" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.dueDate" type="text" placeholder="Filter…"></td>
          <td>
            <select v-model="filters.reconciliation">
              <option value="">All</option>
              <option v-for="r in reconciliationOptions" :key="r" :value="r">{{ r }}</option>
            </select>
          </td>
          <td>
            <select v-model="filters.paymentStatus">
              <option value="">All</option>
              <option v-for="p in paymentStatusOptions" :key="p" :value="p">{{ p }}</option>
            </select>
          </td>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!displayRows.length">
          <td :colspan="vendorOptions ? 9 : 8" class="empty-state">No invoices match these filters.</td>
        </tr>
        <tr
          v-for="entry in displayRows" :key="entry.kind === 'invoice' ? entry.row.id : `${entry.kind}:${entry.po.po_code}`"
          :class="{ 'vr-row': entry.kind === 'invoice' && entry.row.vendor_request }"
        >
          <td v-if="vendorOptions">{{ vendorLabel(entry.kind === 'invoice' ? entry.row.vendor_code : entry.po.vendor_code) }}</td>
          <td class="mono">
            <button
              class="link-btn-inline"
              @click="onOpenPo(entry.kind === 'invoice' ? entry.row.po_code : entry.po.po_code)"
            >{{ entry.kind === 'invoice' ? entry.row.po_code : entry.po.po_code }}</button>
          </td>
          <td class="mono">{{ entry.kind === 'invoice' ? invoiceNumber(entry.row) : '–' }}</td>
          <td class="col-tight">
            <ViewInvoiceButton v-if="entry.kind === 'invoice'" :row="entry.row" />
            <InvoiceUploadButton v-else :on-click="() => openUploadModal(entry.po)" />
          </td>
          <td class="num mono">{{ fmtMoney(entry.kind === 'invoice' ? invoiceValue(entry.row) : entry.po.total_amount) }}</td>
          <td class="num mono">{{ entry.kind === 'invoice' ? fmtMoney(grnValue(entry.row)) : '–' }}</td>
          <td class="mono">
            <template v-if="entry.kind === 'invoice'">
              {{ fmtDateOnly(dueDate(entry.row)) }}
              <span
                v-if="dueDateEstimated(entry.row)" class="due-date-estimated-mark"
                title="Not printed on the invoice -- estimated as 45 days from the invoice date."
              >*</span>
            </template>
            <span v-else class="cell-empty">–</span>
          </td>
          <td>
            <template v-if="entry.kind === 'invoice'">
              <ReconciliationChip :row="entry.row" />
              <button
                v-if="['mismatch', 'needs_review', 'wrong_po'].includes(entry.row.match_status)"
                type="button" class="link-btn-inline recon-why" title="See what was checked and what to do" @click="explainRow = entry.row"
              >Why?</button>
            </template>
            <span v-else-if="entry.kind === 'need_invoice'" class="chip chip-critical">Invoice needed</span>
            <span v-else class="chip chip-muted" title="Finance already has a payment record for this PO -- an invoice copy was never uploaded through the portal.">No invoice uploaded</span>
          </td>
          <td>
            <PaymentStatusChip v-if="entry.kind === 'invoice'" :row="entry.row" :uploader-label="uploaderLabel" />
            <span
              v-else-if="entry.kind === 'po_payment'" class="chip"
              :class="`chip-${paymentStatusClass(entry.po.payment_status)}`"
            >{{ paymentStatusLabel(entry.po.payment_status) }}</span>
            <span v-else class="cell-empty">–</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div></div>
  <p v-if="rows.some(dueDateEstimated)" class="field-hint">* not printed on the invoice -- estimated as 45 days from the invoice date.</p>

  <InvoiceCheckExplainer :row="explainRow" @close="explainRow = null" />

  <InvoiceUploadModal
    :model-value="!!uploadModalPoCode"
    :po-code="uploadModalPoCode || ''"
    :vendor-code="uploadModalVendorCode || ''"
    :uploader-label="uploaderLabel"
    @update:model-value="(v) => { if (!v) uploadModalPoCode = null }"
  />
</template>
