<script setup>
// Health Card › Payment Outlook. Per vendor, three values over the unpaid invoices
// (usePaymentPendency.js, shared with 3.1):
//   Due now        due date today or earlier
//   Due in 0–7d    due date in the next 7 days (tomorrow .. today+7)
//   0–7d · stuck   the part of Due in 0–7d whose 3-way match isn't clean yet
//   Stuck in recon 3-way match (PO × GRN × invoice) not clean -- mismatch, GRN pending,
//                  check failed -- whatever the due date. Overlaps the two due columns on
//                  purpose: it's the slice of what's owed that can't be paid until fixed.
// Click a value to list the invoices behind it.
import { ref, computed } from "vue";
import { fmtMoney, fmtDateOnly } from "../../format.js";
import { unpaidInvoices, byVendor, sumAmount, STUCK, RECON_LABEL } from "../../composables/usePaymentPendency.js";

const props = defineProps({
  title: { type: String, default: "Payment Outlook" },
  rows: { type: Array, required: true },
  vendorLabel: { type: Function, required: true },
});

const next7 = x => x.days != null && x.days < 0 && x.days >= -7; // tomorrow .. today+7
const COLS = [
  { key: "due", label: "Value due", hint: "Unpaid, due date today or earlier", test: x => x.days != null && x.days >= 0, cls: "cell-critical" },
  { key: "next7", label: "Due in 0–7 days", hint: "Unpaid, due in the next 7 days", test: next7, cls: "cell-warn" },
  { key: "next7Stuck", label: "0–7 days · stuck in recon", hint: "Of Due in 0–7 days: PO × GRN × invoice not matched yet", test: x => next7(x) && STUCK.has(x.status), cls: "cell-open" },
  { key: "stuck", label: "Stuck in 3-way recon", hint: "Unpaid and PO × GRN × invoice not matched: mismatch, GRN pending or check failed", test: x => STUCK.has(x.status), cls: "cell-open" },
];

const unpaid = computed(() => unpaidInvoices(props.rows));
const split = list => Object.fromEntries(COLS.map(c => [c.key, list.filter(c.test)]));

const vendors = computed(() => byVendor(unpaid.value, props.vendorLabel)
  .map(v => ({ ...v, cells: split(v.list) }))
  .filter(v => COLS.some(c => v.cells[c.key].length))
  .sort((a, b) => sumAmount(b.cells.due) - sumAmount(a.cells.due) || sumAmount(b.cells.stuck) - sumAmount(a.cells.stuck)));
const total = computed(() => split(unpaid.value));

const sel = ref(null); // { v: vendor code | null (total row), c: col key }
function pick(v, c, list) {
  if (!list.length) return;
  sel.value = sel.value && sel.value.v === v && sel.value.c === c ? null : { v, c };
}
const detail = computed(() => {
  if (!sel.value) return null;
  const col = COLS.find(c => c.key === sel.value.c);
  const v = sel.value.v == null ? null : vendors.value.find(x => x.code === sel.value.v);
  const list = v ? v.cells[col.key] : total.value[col.key];
  return {
    title: `${v ? v.label : "All vendors"} · ${col.label}`,
    list: [...list].sort((a, b) => (b.days ?? -999) - (a.days ?? -999)),
  };
});
const dueTxt = d => (d == null ? "–" : d > 0 ? `${d}d overdue` : d === 0 ? "today" : `in ${-d}d`);
</script>

<template>
  <section class="table-card hc-view">
    <h3 class="card-caption">{{ title }}</h3>
    <div class="table-scroll">
      <table class="hc-table">
        <colgroup><col style="width:28%"><col><col><col><col></colgroup>
        <thead><tr>
          <th>Vendor</th>
          <th v-for="c in COLS" :key="c.key" class="c" :title="c.hint">{{ c.label }}</th>
        </tr></thead>
        <tbody>
          <tr v-if="!vendors.length"><td :colspan="COLS.length + 1" class="hc-muted" style="text-align:center;">Nothing due or stuck</td></tr>
          <tr v-for="v in vendors" :key="v.code">
            <td class="lab">{{ v.label }}</td>
            <td v-for="c in COLS" :key="c.key" class="pc">
              <button type="button" class="hc-pill"
                      :class="[v.cells[c.key].length ? `${c.cls} click` : 'na', { sel: sel && sel.v === v.code && sel.c === c.key }]"
                      :disabled="!v.cells[c.key].length" :title="`${v.cells[c.key].length} invoice(s)`"
                      @click="pick(v.code, c.key, v.cells[c.key])">{{ v.cells[c.key].length ? fmtMoney(sumAmount(v.cells[c.key])) : "–" }}</button>
            </td>
          </tr>
          <tr class="row-total">
            <td class="lab">Total</td>
            <td v-for="c in COLS" :key="c.key" class="pc">
              <button type="button" class="hc-pill" :class="[total[c.key].length ? 'click' : 'na', { sel: sel && sel.v === null && sel.c === c.key }]"
                      :disabled="!total[c.key].length" :title="`${total[c.key].length} invoice(s)`"
                      @click="pick(null, c.key, total[c.key])">{{ total[c.key].length ? fmtMoney(sumAmount(total[c.key])) : "–" }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="card-legend">"0–7 days · stuck in recon" is the part of "Due in 0–7 days" not yet matched. "Stuck in 3-way recon" is every unmatched unpaid invoice, whatever its due date, so it overlaps the due columns.</div>
    <div v-if="detail" class="hc-detail">
      <div class="hc-detail-head">
        <span>{{ detail.title }} ({{ detail.list.length }})</span>
        <button @click="sel = null">Close</button>
      </div>
      <div class="table-scroll">
        <table class="sp-table">
          <thead><tr><th>Vendor</th><th>Invoice</th><th>PO</th><th>Due date</th><th>Due</th><th class="num">Amount</th><th>Reconciliation</th></tr></thead>
          <tbody>
            <tr v-for="x in detail.list" :key="x.id">
              <td>{{ vendorLabel(x.vendorCode) }}</td>
              <td>{{ x.inv }}</td>
              <td>{{ x.po }}</td>
              <td>{{ fmtDateOnly(x.due) }}<span v-if="x.estimated" class="hc-muted" title="Not printed on the invoice -- estimated as 45 days from the invoice date">*</span></td>
              <td class="hc-num">{{ dueTxt(x.days) }}</td>
              <td class="num hc-num">{{ fmtMoney(x.amount) }}</td>
              <td>{{ RECON_LABEL[x.status] || x.status || "–" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
