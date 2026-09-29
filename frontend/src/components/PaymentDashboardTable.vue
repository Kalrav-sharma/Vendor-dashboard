<script setup>
import { computed, ref } from "vue";
import { fmtMoney, fmtDateOnly } from "../format.js";
import ReconciliationChip from "./ReconciliationChip.vue";
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
  showBuckets: { type: Boolean, default: false }, // vendor.html's All/CN Required/Invoice Copy Needed/No Action Needed/Paid tabs
  posNeedingInvoice: { type: Array, default: () => [] }, // POs with no invoice uploaded yet at all -- vendor-only, see posNeedingInvoice in VendorApp.vue
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
// missing its invoice copy (posNeedingInvoice, passed in from
// VendorApp.vue since that's PO-level data this table doesn't otherwise
// have) OR an uploaded invoice flagged for a credit note (match_status
// "mismatch") -- an already-paid invoice can't be in either group.
const vendorKpiTiles = computed(() => {
  const paidRows = props.rows.filter(isPaid);
  const madeTillDate = paidRows.reduce((s, r) => s + (invoiceValue(r) || 0), 0);

  const weekCutoff = Date.now() - SEVEN_DAYS_MS;
  const paidLastWeekRows = paidRows.filter(r => r.payment_date && new Date(r.payment_date).getTime() >= weekCutoff);
  const paidLastWeek = paidLastWeekRows.reduce((s, r) => s + (invoiceValue(r) || 0), 0);

  const toBePaidRows = props.rows.filter(r => r.payment_status === "pending" && (invoiceValue(r) || 0) > 0);
  const toBePaid = toBePaidRows.reduce((s, r) => s + (invoiceValue(r) || 0), 0);

  const creditNoteNeededCount = props.rows.filter(r => r.match_status === "mismatch").length;
  const actionRequired = props.posNeedingInvoice.length + creditNoteNeededCount;

  return [
    { label: "Payments made till date", value: fmtMoney(madeTillDate), sublabel: `${paidRows.length} invoices` },
    { label: "Paid last week", value: fmtMoney(paidLastWeek), sublabel: `${paidLastWeekRows.length} invoices` },
    { label: "To be paid this week", value: fmtMoney(toBePaid), sublabel: `${toBePaidRows.length} invoices` },
    { label: "Action required", value: `${actionRequired} invoices`, cls: actionRequired > 0 ? "critical" : "" },
  ];
});

const adminKpiTiles = computed(() => {
  const pending = props.rows.filter(r => !isPaid(r)).length;
  const onTrack = props.rows.filter(r => r.match_status === "matched" && !isOverdue(r)).length;
  const hasIssues = props.rows.filter(r => r.match_status === "mismatch" || r.match_status === "error").length;
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
// clickable buckets -- "All" is every real invoice row PLUS the synthetic
// "needs a copy" PO entries below, so its count matches the sum of the
// other four exactly (mirrors why Action Required above adds those two
// different-shaped things together). No-Action-Needed is deliberately the
// leftover bucket: not yet paid and not flagged for a credit note --
// covers pending/matched/needs_review/error alike, since none of those
// need anything from the vendor beyond waiting.
const BUCKETS = [
  { key: "all", label: "All" },
  { key: "cn_required", label: "CN Required" },
  { key: "invoice_copy_needed", label: "Invoice Copy Needed" },
  { key: "no_action_needed", label: "No Action Needed" },
  { key: "paid", label: "Paid" },
];
const cnRequiredRows = computed(() => props.rows.filter((r) => r.match_status === "mismatch"));
const paidRows = computed(() => props.rows.filter(isPaid));
const noActionRows = computed(() => props.rows.filter((r) => !isPaid(r) && r.match_status !== "mismatch"));
const activeBucket = ref("all");
const bucketCounts = computed(() => ({
  all: props.rows.length + props.posNeedingInvoice.length,
  cn_required: cnRequiredRows.value.length,
  invoice_copy_needed: props.posNeedingInvoice.length,
  no_action_needed: noActionRows.value.length,
  paid: paidRows.value.length,
}));

// Real invoice rows and "needs a copy" POs have different shapes, so each
// display row is tagged with its kind and the template branches per cell.
function invoiceEntry(row) { return { kind: "invoice", row }; }
function needInvoiceEntry(po) { return { kind: "need_invoice", po }; }
const displayRows = computed(() => {
  if (!props.showBuckets) return props.rows.map(invoiceEntry);
  switch (activeBucket.value) {
    case "cn_required": return cnRequiredRows.value.map(invoiceEntry);
    case "invoice_copy_needed": return props.posNeedingInvoice.map(needInvoiceEntry);
    case "no_action_needed": return noActionRows.value.map(invoiceEntry);
    case "paid": return paidRows.value.map(invoiceEntry);
    default: return [...props.rows.map(invoiceEntry), ...props.posNeedingInvoice.map(needInvoiceEntry)];
  }
});
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div v-if="showBuckets" class="bucket-tabs">
    <button
      v-for="b in BUCKETS" :key="b.key" type="button"
      class="bucket-tab" :class="{ active: activeBucket === b.key }"
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
        <tr v-if="!rows.length">
          <td :colspan="vendorOptions ? 9 : 8" class="empty-state">No invoices match these filters.</td>
        </tr>
        <tr v-for="row in rows" :key="row.id">
          <td v-if="vendorOptions">{{ vendorLabel(row.vendor_code) }}</td>
          <td class="mono"><button class="link-btn-inline" @click="onOpenPo(row.po_code)">{{ row.po_code }}</button></td>
          <td class="mono">{{ invoiceNumber(row) }}</td>
          <td class="col-tight"><ViewInvoiceButton :row="row" /></td>
          <td class="num mono">{{ fmtMoney(invoiceValue(row)) }}</td>
          <td class="num mono">{{ fmtMoney(grnValue(row)) }}</td>
          <td class="mono">
            {{ fmtDateOnly(dueDate(row)) }}
            <span
              v-if="dueDateEstimated(row)" class="due-date-estimated-mark"
              title="Not printed on the invoice -- estimated as 45 days from the invoice date."
            >*</span>
          </td>
          <td><ReconciliationChip :row="row" /></td>
          <td><PaymentStatusChip :row="row" :uploader-label="uploaderLabel" /></td>
        </tr>
      </tbody>
    </table>
  </div></div>
  <p v-if="rows.some(dueDateEstimated)" class="field-hint">* not printed on the invoice -- estimated as 45 days from the invoice date.</p>
</template>
