<script setup>
// Health Card › Delayed Orders. Spares | Refresh toggle. Order weeks × mutually exclusive
// bands of days past promise: >3 (4–5), >5 (6–10), >10 (11–15), >15 (16+).
import { ref, computed } from "vue";

const props = defineProps({
  spares: { type: Array, required: true },
  refreshKit: { type: Array, required: true },
});
const product = ref("spares");
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
</script>

<template>
  <section class="table-card hc-view">
    <div class="card-caption hc-caption">
      <span>Delayed Orders</span>
      <span class="hc-toggle">
        <button :class="{ active: product === 'spares' }" @click="product = 'spares'">Spares</button>
        <button :class="{ active: product === 'refresh' }" @click="product = 'refresh'">Refresh</button>
      </span>
    </div>
    <table class="hc-table">
      <colgroup><col style="width:16%"><col style="width:14%"><col><col><col><col></colgroup>
      <thead><tr><th>Week</th><th class="num">Orders</th><th v-for="b in BANDS" :key="b.key" class="c">{{ b.label }}</th></tr></thead>
      <tbody>
        <tr v-for="w in weeks" :key="w.weekStart" :title="range(w.weekStart)">
          <td class="lab">W{{ w.weekNo ?? '–' }}<span v-if="w.current" class="hc-live"></span></td>
          <td class="num hc-num hc-muted">{{ w.orders == null ? '–' : w.orders.toLocaleString('en-IN') }}</td>
          <td v-for="b in BANDS" :key="b.key" class="pc"><span class="hc-pill" :class="cls(w, b)">{{ w[b.key] ?? '–' }}</span></td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
