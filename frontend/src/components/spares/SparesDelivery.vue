<script setup>
// Spares › Spares Delivery (moved from Health Card › Delayed Orders on 2026-10-05). Only orders
// IN TRANSIT WITH THE LSP and already past promise: the sync verifies every Jarvis-open order in
// live Uniware (dispatched, not RTO/undelivered/lost/delivered/cancelled) since 2026-10-09; orders
// not dispatched yet are excluded too. Spares | Refresh toggle and # | % toggle
// (% = band count ÷ that week's orders). Order weeks × mutually exclusive bands of days past
// promise: >3 (4–5), >5 (6–10), >10 (11–15), >15 (16+). CSV = the orders behind every cell of the selected product, sorted by LSP for forwarding (LSP / AWB / status from Uniware).
import { ref, computed } from "vue";
import { downloadCsv } from "../sla/slaUtil.js";

const props = defineProps({
  title: { type: String, default: "Spares Delivery" },
  spares: { type: Array, required: true },
  refreshKit: { type: Array, required: true },
  fetchOrders: { type: Function, required: true },
});
const product = ref("spares");
const mode = ref("n"); // "n" | "pct"
const weeks = computed(() => (product.value === "spares" ? props.spares : props.refreshKit));
const BANDS = [
  { key: "d3", label: "> 3 days", tint: "cell-warn" },
  { key: "d5", label: "> 5 days", tint: "cell-open" },
  { key: "d10", label: "> 10 days", tint: "cell-critical" },
  { key: "d15", label: "> 15 days", tint: "cell-critical" },
];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const range = ws => {
  const s = new Date(`${ws}T00:00:00Z`), e = new Date(s.getTime() + 6 * 86400000);
  return `${s.getUTCDate()} ${MONTHS[s.getUTCMonth()]} – ${e.getUTCDate()} ${MONTHS[e.getUTCMonth()]}`;
};
const cls = (w, b) => (w[b.key] == null ? "na" : w[b.key] ? b.tint : "cell-good");
const cell = (w, b) => {
  const v = w[b.key];
  if (v == null) return "–";
  if (mode.value === "n") return v;
  return w.orders ? `${((v / w.orders) * 100).toFixed(1)}%` : "–";
};

const busy = ref(false);
const csvError = ref("");
async function download() {
  busy.value = true;
  csvError.value = "";
  try {
    const p = product.value, name = p === "refresh" ? "Refresh" : "Spares";
    const rows = (await props.fetchOrders()).filter(r => r.product === p);
    if (!rows.length) { csvError.value = `No in-transit delayed ${name} orders.`; return; }
    const headers = ["LSP", "AWB", "Order ID", "Product", "Week", "Order date", "Promised delivery", "Days past promise", "Band",
      "Current status (Uniware)", "Dispatched on", "City", "WH", "Partner type", "SKU"];
    const sorted = [...rows].sort((a, b) => (a.dsp || "").localeCompare(b.dsp || "") || b.delay_days - a.delay_days);
    const out = sorted.map(r => [r.dsp, r.docket_no, r.order_code, r.product === "refresh" ? "Refresh" : "Spares",
      r.week_no != null ? `W${r.week_no}` : "", r.order_date, r.promised_date, r.delay_days, r.band, r.shipment_status,
      r.shipped_date, r.city, r.wh, r.partner, r.sku]);
    downloadCsv(`in_transit_delayed_${p}_${new Date().toISOString().slice(0, 10)}.csv`, headers, out);
  } catch (e) {
    csvError.value = `Couldn't fetch delayed orders: ${e.message}`;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="table-card hc-view">
    <div class="card-caption hc-caption">
      <span>{{ title }}</span>
      <span class="hc-caption-tools">
        <span v-if="csvError" class="hc-csv-err">{{ csvError }}</span>
        <span class="hc-toggle">
          <button :class="{ active: mode === 'n' }" @click="mode = 'n'">#</button>
          <button :class="{ active: mode === 'pct' }" @click="mode = 'pct'">%</button>
        </span>
        <span class="hc-toggle">
          <button :class="{ active: product === 'spares' }" @click="product = 'spares'">Spares</button>
          <button :class="{ active: product === 'refresh' }" @click="product = 'refresh'">Refresh</button>
        </span>
        <span class="hc-toggle">
          <button :disabled="busy" :title="`In-transit delayed ${product === 'refresh' ? 'Refresh' : 'Spares'} orders with LSP + AWB, all weeks shown`" @click="download">{{ busy ? "…" : "CSV ↓" }}</button>
        </span>
      </span>
    </div>
    <table class="hc-table">
      <colgroup><col style="width:16%"><col style="width:14%"><col><col><col><col></colgroup>
      <thead><tr><th>Week</th><th class="num">Orders</th><th v-for="b in BANDS" :key="b.key" class="c">{{ b.label }}</th></tr></thead>
      <tbody>
        <tr v-for="w in weeks" :key="w.weekStart" :title="range(w.weekStart)">
          <td class="lab">W{{ w.weekNo ?? '–' }}<span v-if="w.current" class="hc-live"></span></td>
          <td class="num hc-num hc-muted">{{ w.orders == null ? '–' : w.orders.toLocaleString('en-IN') }}</td>
          <td v-for="b in BANDS" :key="b.key" class="pc"><span class="hc-pill" :class="cls(w, b)">{{ cell(w, b) }}</span></td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
