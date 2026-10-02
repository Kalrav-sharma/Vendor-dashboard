<script setup>
// The KPI tiles + breakdown sections both Finance pages share -- the executive summary
// passes every vendor's summarise(), Vendor Payments passes one vendor's, so the two pages
// read identically and a vendor's figures are always a slice of the all-vendor ones.
import { ref, computed, watch } from "vue";
import { fmtMoney, fmtMoneyCompact, fmtDateOnly, paymentStatusLabel } from "../../format.js";
import { RECON_LABEL } from "../../composables/usePaymentPendency.js";
import SummaryKpis from "../SummaryKpis.vue";
import PaymentBreakdownCard from "./PaymentBreakdownCard.vue";
import "./payments.css";

const props = defineProps({
  summary: { type: Object, required: true }, // summarise() from usePaymentSummary.js
  showVendor: { type: Boolean, default: false }, // multi-vendor: add a Vendor column to drill-down lists
  vendorLabel: { type: Function, default: (code) => code },
  onOpenPo: { type: Function, required: true }, // (poCode) => void
});

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const pctOf = (part, whole) => (whole > 0 ? `${Math.round((part / whole) * 100)}%` : "–");

const kpiRows = computed(() => {
  const s = props.summary;
  const sum = (l) => l.reduce((t, e) => t + (e.amount || 0), 0);
  return [
    [
      {
        label: "Total invoiced", value: fmtMoneyCompact(s.invoiced),
        sublabel: `${plural(s.entries.length, "invoice")}${props.showVendor ? ` · ${plural(s.vendorCount, "vendor")}` : ""}`,
      },
      { label: "Paid", value: fmtMoneyCompact(s.paidAmount), cls: "good", sublabel: `${plural(s.paid.length, "invoice")} · ${pctOf(s.paidAmount, s.invoiced)} of invoiced` },
      { label: "Outstanding", value: fmtMoneyCompact(s.unpaidAmount), sublabel: plural(s.unpaid.length, "invoice") },
      {
        label: "Overdue", value: fmtMoneyCompact(sum(s.overdue)), cls: s.overdue.length ? "critical" : "",
        sublabel: s.overdue.length ? `${plural(s.overdue.length, "invoice")} · oldest ${s.oldestOverdue}d` : "Nothing past due",
      },
    ],
    [
      { label: "Due in next 7 days", value: fmtMoneyCompact(sum(s.dueSoon)), sublabel: plural(s.dueSoon.length, "invoice") },
      {
        label: "Stuck in reconciliation", value: fmtMoneyCompact(sum(s.stuck)), cls: s.stuck.length ? "critical" : "",
        sublabel: `${plural(s.stuck.length, "invoice")} · mismatch, GRN pending or check failed`,
      },
      {
        label: "Awaiting invoice", value: plural(s.awaiting.length, "PO"), cls: s.awaiting.length ? "critical" : "",
        sublabel: `${fmtMoneyCompact(s.awaitingAmount)} PO value · since 1 Aug`,
      },
      {
        label: "Avg days to pay", value: s.daysToPay == null ? "–" : `${s.daysToPay}d`,
        sublabel: s.daysToPaySample ? `invoice upload → payment · ${plural(s.daysToPaySample, "invoice")}` : "No paid invoices with a payment date yet",
      },
    ],
  ];
});

// One open drill-down per page: { card, key }
const CARDS = { ageing: "Outstanding by age", recon: "Reconciliation status", payment: "Payment status" };
const sel = ref(null);
function pick(card, key) {
  sel.value = sel.value && sel.value.card === card && sel.value.key === key ? null : { card, key };
}
// a different vendor (or fresh data) can leave the open bucket pointing at nothing
watch(() => props.summary, () => {
  if (sel.value && !props.summary[sel.value.card].find((b) => b.key === sel.value.key)?.count) sel.value = null;
});
const detail = computed(() => {
  if (!sel.value) return null;
  const b = props.summary[sel.value.card].find((x) => x.key === sel.value.key);
  if (!b) return null;
  return {
    title: `${CARDS[sel.value.card]} · ${b.label}`, amount: b.amount,
    list: [...b.list].sort((x, y) => (y.days ?? -9999) - (x.days ?? -9999) || (y.amount || 0) - (x.amount || 0)),
  };
});

const dueTxt = (d) => (d == null ? "–" : d > 0 ? `${d}d overdue` : d === 0 ? "today" : `in ${-d}d`);
function reconTxt(e) {
  if (e.kind === "po") return "No portal invoice";
  if (e.recon === "mismatch") return e.cnSubmitted ? "Mismatch · CN submitted" : "Mismatch · CN pending";
  return RECON_LABEL[e.recon] || "Checking";
}
const payTxt = (e) => (e.pay === "none" ? "Not booked" : paymentStatusLabel(e.pay === "booked" ? "pending" : "paid"));
const payCls = (e) => (e.pay === "paid" ? "chip-good" : e.pay === "booked" ? "chip-open" : "chip-muted");

const maxMonth = computed(() => Math.max(1, ...props.summary.monthly.flatMap((m) => [m.receivedAmount, m.paidAmount])));
</script>

<template>
  <div class="pay-kpi-rows">
    <SummaryKpis v-for="(row, i) in kpiRows" :key="i" :tiles="row" />
  </div>

  <div class="pay-grid">
    <PaymentBreakdownCard
      title="Outstanding by age" sub="unpaid value"
      :buckets="summary.ageing" :selected="sel?.card === 'ageing' ? sel.key : null"
      note="Due dates not printed on an invoice are estimated as invoice date + 45 days."
      @pick="(k) => pick('ageing', k)"
    />
    <PaymentBreakdownCard
      title="Reconciliation status" sub="unpaid value"
      :buckets="summary.recon" :selected="sel?.card === 'recon' ? sel.key : null"
      note="3-way match of PO × GRN × invoice. Anything but Reconciled can't be paid until it's fixed."
      @pick="(k) => pick('recon', k)"
    />
    <PaymentBreakdownCard
      title="Payment status" sub="all invoices"
      :buckets="summary.payment" :selected="sel?.card === 'payment' ? sel.key : null"
      note="From the payout file sync. Not booked = no payout record synced for it yet."
      @pick="(k) => pick('payment', k)"
    />
  </div>

  <div v-if="detail" class="pay-detail">
    <div class="pay-detail-head">
      <span>{{ detail.title }} · {{ plural(detail.list.length, "invoice") }} · {{ fmtMoney(detail.amount) }}</span>
      <button type="button" @click="sel = null">Close</button>
    </div>
    <div class="table-scroll">
      <table>
        <thead><tr>
          <th v-if="showVendor">Vendor</th><th>PO code</th><th>Invoice number</th>
          <th>Due date</th><th>Due</th><th class="num">Amount</th><th>Reconciliation</th><th>Payment</th>
        </tr></thead>
        <tbody>
          <tr v-for="e in detail.list" :key="e.key">
            <td v-if="showVendor">{{ vendorLabel(e.vendorCode) }}</td>
            <td class="mono"><button type="button" class="link-btn-inline" @click="onOpenPo(e.po)">{{ e.po }}</button></td>
            <td class="mono">{{ e.inv }}</td>
            <td class="mono">{{ fmtDateOnly(e.due) }}<span v-if="e.estimated" class="due-date-estimated-mark" title="Not printed on the invoice -- estimated as 45 days from the invoice date.">*</span></td>
            <td class="mono" :class="{ critical: e.days > 0 && e.pay !== 'paid' }">{{ e.pay === "paid" ? "–" : dueTxt(e.days) }}</td>
            <td class="num mono">{{ fmtMoney(e.amount) }}</td>
            <td>{{ reconTxt(e) }}</td>
            <td><span class="chip" :class="payCls(e)">{{ payTxt(e) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <h3 class="pay-section-title">Monthly flow</h3>
  <div class="table-card" style="margin-bottom: 16px;"><div class="table-scroll">
    <table class="pay-table">
      <thead><tr>
        <th>Month</th>
        <th class="num">Invoices received</th><th class="num">Value received</th>
        <th class="num">Invoices paid</th><th class="num">Value paid</th>
        <th style="width: 34%;">Received vs paid</th>
      </tr></thead>
      <tbody>
        <tr v-for="m in summary.monthly" :key="m.key">
          <td class="lab">{{ m.label }}</td>
          <td class="num mono">{{ m.received.length }}</td>
          <td class="num mono">{{ fmtMoney(m.receivedAmount) }}</td>
          <td class="num mono">{{ m.paid.length }}</td>
          <td class="num mono good">{{ fmtMoney(m.paidAmount) }}</td>
          <td>
            <div class="pay-flow" :title="`Received ${fmtMoney(m.receivedAmount)} · Paid ${fmtMoney(m.paidAmount)}`">
              <div class="meter-track"><div class="meter-fill pay-flow-in" :style="{ width: (m.receivedAmount / maxMonth) * 100 + '%' }"></div></div>
              <div class="meter-track"><div class="meter-fill pay-flow-out" :style="{ width: (m.paidAmount / maxMonth) * 100 + '%' }"></div></div>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div></div>
  <p class="field-hint" style="margin: -6px 0 16px;">
    Received = invoice uploaded to the portal that month; paid = payment date from the payout file. Top bar received, bottom bar paid.
    <template v-if="summary.noValue"> {{ plural(summary.noValue, "invoice") }} had no value read off the PDF and count as ₹0.</template>
  </p>
</template>
