<script setup>
// Finance › Payment Dashboard: the executive summary of every vendor's invoices -- KPI tiles,
// the shared breakdown sections, and a vendor-wise rollup. No per-invoice table here: the
// rollup's vendor names open Vendor Payments scoped to that vendor, and every breakdown
// bucket still drills to the invoices behind it.
import { computed } from "vue";
import { fmtMoney } from "../../format.js";
import { paymentLedger, summarise, vendorRollup, sumAmount } from "../../composables/usePaymentSummary.js";
import PaymentSummaryPanels from "./PaymentSummaryPanels.vue";

const props = defineProps({
  uploads: { type: Array, required: true }, // allUploads (po_invoice_uploads)
  posWithPayment: { type: Array, default: () => [] }, // booked/paid POs with no portal invoice
  posNeedingInvoice: { type: Array, default: () => [] }, // no invoice, no payment record
  vendorLabel: { type: Function, required: true },
  onOpenPo: { type: Function, required: true },
  onOpenVendor: { type: Function, required: true }, // (vendorCode) => void -- switches to Vendor Payments
});

const ledger = computed(() => paymentLedger(props.uploads, props.posWithPayment));
const summary = computed(() => summarise(ledger.value, props.posNeedingInvoice));
const vendors = computed(() => vendorRollup(ledger.value, props.posNeedingInvoice, props.vendorLabel));
</script>

<template>
  <PaymentSummaryPanels :summary="summary" :show-vendor="true" :vendor-label="vendorLabel" :on-open-po="onOpenPo" />

  <h3 class="pay-section-title">Vendor-wise summary</h3>
  <div class="table-card"><div class="table-scroll">
    <table class="pay-table">
      <thead><tr>
        <th>Vendor</th>
        <th class="num">Invoices</th><th class="num">Invoiced</th><th class="num">Paid</th>
        <th class="num">Outstanding</th><th class="num">Overdue</th>
        <th class="num" title="Unpaid with mismatch, GRN pending or check failed">Stuck in recon</th>
        <th class="num" title="POs since 1 Aug with no invoice uploaded and no payment record">Awaiting invoice</th>
        <th class="num" title="Average days from invoice upload to payment">Avg days to pay</th>
      </tr></thead>
      <tbody>
        <tr v-if="!vendors.length"><td colspan="9" class="empty-state">No invoices yet.</td></tr>
        <tr v-for="v in vendors" :key="v.code">
          <td class="lab"><button type="button" class="link-btn-inline" title="Open this vendor's payment view" @click="onOpenVendor(v.code)">{{ v.label }}</button></td>
          <td class="num mono">{{ v.s.entries.length }}</td>
          <td class="num mono">{{ fmtMoney(v.s.invoiced) }}</td>
          <td class="num mono">{{ fmtMoney(v.s.paidAmount) }}</td>
          <td class="num mono">{{ fmtMoney(v.s.unpaidAmount) }}</td>
          <td class="num mono" :class="{ critical: v.s.overdue.length }">{{ v.s.overdue.length ? fmtMoney(sumAmount(v.s.overdue)) : "–" }}</td>
          <td class="num mono" :class="{ critical: v.s.stuck.length }">{{ v.s.stuck.length ? fmtMoney(sumAmount(v.s.stuck)) : "–" }}</td>
          <td class="num mono" :class="{ critical: v.s.awaiting.length }">{{ v.s.awaiting.length || "–" }}</td>
          <td class="num mono">{{ v.s.daysToPay == null ? "–" : `${v.s.daysToPay}d` }}</td>
        </tr>
        <tr v-if="vendors.length" class="row-total">
          <td class="lab">Total</td>
          <td class="num mono">{{ summary.entries.length }}</td>
          <td class="num mono">{{ fmtMoney(summary.invoiced) }}</td>
          <td class="num mono">{{ fmtMoney(summary.paidAmount) }}</td>
          <td class="num mono">{{ fmtMoney(summary.unpaidAmount) }}</td>
          <td class="num mono">{{ fmtMoney(sumAmount(summary.overdue)) }}</td>
          <td class="num mono">{{ fmtMoney(sumAmount(summary.stuck)) }}</td>
          <td class="num mono">{{ summary.awaiting.length }}</td>
          <td class="num mono">{{ summary.daysToPay == null ? "–" : `${summary.daysToPay}d` }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
