<script setup>
// Health Card › Overdue Payments. One row per vendor: unpaid invoices whose due date has
// passed, their invoice value, and how many days overdue (oldest + value-weighted average).
// Rows come from usePaymentPendency.js (shared with 3.2: unpaid, due date, dedupe). Unlike
// the Payment Dashboard's "Overdue" tile this counts reconciled invoices too: a matched
// invoice past its due date is the core of payment pendency. Click a vendor to list its
// invoices.
import { ref, computed } from "vue";
import { fmtMoney, fmtDateOnly } from "../../format.js";
import { unpaidInvoices, byVendor, sumAmount, RECON_LABEL } from "../../composables/usePaymentPendency.js";

const props = defineProps({
  title: { type: String, default: "Overdue Payments" },
  rows: { type: Array, required: true },
  vendorLabel: { type: Function, required: true },
});

// due today isn't overdue yet
const overdue = computed(() => unpaidInvoices(props.rows).filter(x => x.days != null && x.days > 0));

function summarise(list) {
  const amount = sumAmount(list);
  const wDays = list.reduce((s, x) => s + (x.amount || 0) * x.days, 0);
  return {
    count: list.length, amount,
    oldest: list.reduce((m, x) => Math.max(m, x.days), 0),
    avg: amount > 0 ? Math.round(wDays / amount) : null,
    noValue: list.filter(x => x.amount == null).length,
  };
}

const vendors = computed(() => byVendor(overdue.value, props.vendorLabel)
  .map(v => ({ ...v, ...summarise(v.list) }))
  .sort((a, b) => b.amount - a.amount || b.oldest - a.oldest));
const total = computed(() => summarise(overdue.value));

const ageCls = d => (d == null ? "na" : d > 30 ? "cell-critical" : d > 7 ? "cell-open" : "cell-warn");
const sel = ref(null);
const detail = computed(() => {
  const v = vendors.value.find(x => x.code === sel.value);
  return v ? { ...v, list: [...v.list].sort((a, b) => b.days - a.days) } : null;
});
</script>

<template>
  <section class="table-card hc-view">
    <h3 class="card-caption">{{ title }}</h3>
    <div class="table-scroll">
      <table class="hc-table">
        <colgroup><col style="width:34%"><col><col><col><col></colgroup>
        <thead><tr>
          <th>Vendor</th>
          <th class="num">Invoices</th>
          <th class="num">Overdue amount</th>
          <th class="c" title="Days past due date of the oldest overdue invoice">Oldest</th>
          <th class="num" title="Average days overdue, weighted by invoice value">Avg days</th>
        </tr></thead>
        <tbody>
          <tr v-if="!vendors.length"><td colspan="5" class="hc-muted" style="text-align:center;">No overdue invoices</td></tr>
          <tr v-for="v in vendors" :key="v.code">
            <td class="lab"><button type="button" class="link-btn-inline" @click="sel = sel === v.code ? null : v.code">{{ v.label }}</button></td>
            <td class="num hc-num">{{ v.count }}</td>
            <td class="num hc-num">{{ fmtMoney(v.amount) }}<span v-if="v.noValue" class="hc-muted" :title="`${v.noValue} invoice(s) with no value read`">*</span></td>
            <td class="pc"><span class="hc-pill" :class="ageCls(v.oldest)">{{ v.oldest }}d</span></td>
            <td class="num hc-num">{{ v.avg == null ? "–" : `${v.avg}d` }}</td>
          </tr>
          <tr class="row-total">
            <td class="lab">Total</td>
            <td class="num hc-num">{{ total.count }}</td>
            <td class="num hc-num">{{ fmtMoney(total.amount) }}</td>
            <td class="c hc-num">{{ total.count ? `${total.oldest}d` : "–" }}</td>
            <td class="num hc-num">{{ total.avg == null ? "–" : `${total.avg}d` }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="detail" class="hc-detail">
      <div class="hc-detail-head">
        <span>{{ detail.label }} · overdue invoices ({{ detail.list.length }})</span>
        <button @click="sel = null">Close</button>
      </div>
      <div class="table-scroll">
        <table class="sp-table">
          <thead><tr><th>Invoice</th><th>PO</th><th>Due date</th><th class="num">Days overdue</th><th class="num">Amount</th><th>Reconciliation</th></tr></thead>
          <tbody>
            <tr v-for="x in detail.list" :key="x.id">
              <td>{{ x.inv }}</td>
              <td>{{ x.po }}</td>
              <td>{{ fmtDateOnly(x.due) }}<span v-if="x.estimated" class="hc-muted" title="Not printed on the invoice -- estimated as 45 days from the invoice date">*</span></td>
              <td class="num hc-num">{{ x.days }}</td>
              <td class="num hc-num">{{ fmtMoney(x.amount) }}</td>
              <td>{{ RECON_LABEL[x.status] || x.status || "–" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
