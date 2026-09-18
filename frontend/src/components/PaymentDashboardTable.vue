<script setup>
import { computed } from "vue";
import { fmtMoney, fmtDateOnly } from "../format.js";
import ReconciliationChip from "./ReconciliationChip.vue";
import PaymentStatusChip from "./PaymentStatusChip.vue";
import ViewInvoiceButton from "./ViewInvoiceButton.vue";
import SummaryKpis from "./SummaryKpis.vue";

const props = defineProps({
  rows: { type: Array, required: true },        // already filtered
  filters: { type: Object, required: true },     // reactive filter state, mutated directly (v-model)
  reconciliationOptions: { type: Array, required: true }, // distinct reconciliation labels present in the data
  paymentStatusOptions: { type: Array, default: () => [] }, // distinct payment status labels present in the data
  vendorOptions: { type: Array, default: null }, // [{code, label}] -- null hides the Vendor column entirely
  vendorLabel: { type: Function, default: null }, // (code) => string -- required when vendorOptions is set
  onOpenPo: { type: Function, required: true }, // (poCode) => void
  isInternalStaff: { type: Boolean, default: false }, // see reconciliation.js -- admin.html passes true explicitly
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

const kpiTiles = computed(() => {
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
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

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
          <td><ReconciliationChip :row="row" :is-internal-staff="isInternalStaff" /></td>
          <td><PaymentStatusChip :status="row.payment_status" /></td>
        </tr>
      </tbody>
    </table>
  </div></div>
  <p v-if="rows.some(dueDateEstimated)" class="field-hint">* not printed on the invoice -- estimated as 45 days from the invoice date.</p>
</template>
