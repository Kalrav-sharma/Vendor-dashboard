<script setup>
// Health Card › SLA Adherence per week. Days | % toggle: average SLA (delivery days) or on-time %
// (delivered by promise date). Delivered orders only.
//   RO | Locks: split by the carrying LSP (Pan India, DTDC Raftaar, SFX dark store, Other).
//     Rows as in SLA & Demand Share: next week (once it starts), current week, 4 previous.
//   Spares | Refresh: Orders (Pan India delivered) then Top 5 / Next 4 / Other city tiers, by
//     order week (current + 4 previous). See useHealthKitSlaData.js for the sources.
import { ref, computed } from "vue";
import { ADH_LSPS } from "../../composables/useHealthSlaData.js";
import { KIT_TIERS } from "../../composables/useHealthKitSlaData.js";

const props = defineProps({
  title: { type: String, default: "SLA Adherence" },
  ro: { type: Array, required: true },
  locks: { type: Array, required: true },
  spares: { type: Array, default: () => [] },
  refreshKit: { type: Array, default: () => [] },
});
const PRODUCTS = [
  { key: "ro", label: "RO" },
  { key: "locks", label: "Locks" },
  { key: "spares", label: "Spares" },
  { key: "refresh", label: "Refresh" },
];
const product = ref("ro");
const mode = ref("days");
const isKit = computed(() => product.value === "spares" || product.value === "refresh");
const weeks = computed(() => ({ ro: props.ro, locks: props.locks, spares: props.spares, refresh: props.refreshKit }[product.value]));
// Value columns: LSPs for RO/Locks (read from w.adh), city tiers for Spares/Refresh (w.tiers).
const cols = computed(() => (isKit.value ? KIT_TIERS : ADH_LSPS));
const cellOf = (w, key) => (isKit.value ? w.tiers?.[key] : w.adh?.[key]);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const range = ws => {
  const s = new Date(`${ws}T00:00:00Z`), e = new Date(s.getTime() + 6 * 86400000);
  return `${s.getUTCDate()} ${MONTHS[s.getUTCMonth()]} – ${e.getUTCDate()} ${MONTHS[e.getUTCMonth()]}`;
};
const rate = c => (c && c.total > 0 ? Math.round((c.onTime / c.total) * 1000) / 10 : null);
const avgDays = c => (c && c.tatN > 0 ? Math.round((c.tatSum / c.tatN) * 10) / 10 : null);
const val = c => (mode.value === "days" ? avgDays(c) : rate(c));
const cls = p => (p == null ? "na" : p < 70 ? "cell-critical" : p >= 90 ? "cell-good" : "cell-open");
// Arrow compares the displayed values (1 dp). Higher % is better; fewer days is better.
function delta(i, key) {
  const cur = val(cellOf(weeks.value[i] || {}, key)), prev = val(cellOf(weeks.value[i + 1] || {}, key));
  if (cur == null || prev == null || cur === prev) return null;
  const better = mode.value === "days" ? cur < prev : cur > prev;
  return { cls: better ? "good" : "bad", t: cur > prev ? "▲" : "▼" };
}
const tip = c => {
  if (!c || !(c.total || c.tatN)) return "No delivered orders";
  return mode.value === "days" ? `${c.tatN.toLocaleString("en-IN")} delivered` : `${c.onTime.toLocaleString("en-IN")} / ${c.total.toLocaleString("en-IN")} on time`;
};
// Spares/Refresh Orders column: Pan India delivered orders behind the mode shown (Refresh's
// days and on-time come from different queries, so the two counts can differ).
const kitOrders = w => {
  const c = w.tiers?.pan;
  const n = c ? (mode.value === "days" ? c.tatN : c.total) : null;
  return n ? n.toLocaleString("en-IN") : "–";
};
</script>

<template>
  <section class="table-card hc-view">
    <div class="card-caption hc-caption">
      <span>{{ title }}</span>
      <span>
        <span class="hc-toggle" style="margin-right:6px;">
          <button :class="{ active: mode === 'days' }" @click="mode = 'days'">Days</button>
          <button :class="{ active: mode === 'pct' }" @click="mode = 'pct'">%</button>
        </span>
        <span class="hc-toggle">
          <button v-for="p in PRODUCTS" :key="p.key" :class="{ active: product === p.key }" @click="product = p.key">{{ p.label }}</button>
        </span>
      </span>
    </div>
    <table class="hc-table">
      <colgroup v-if="isKit"><col style="width:16%"><col style="width:14%"><col><col><col></colgroup>
      <colgroup v-else><col style="width:16%"><col><col><col><col></colgroup>
      <thead><tr>
        <th>Week</th>
        <th v-if="isKit" class="num">Orders</th>
        <th v-for="l in cols" :key="l.key" class="c" :title="l.hint">{{ l.label }}</th>
      </tr></thead>
      <tbody>
        <tr v-for="(w, i) in weeks" :key="w.weekStart" :title="range(w.weekStart)">
          <td class="lab">W{{ w.weekNo ?? '–' }}<span v-if="w.kind === 'current'" class="hc-live"></span><span v-if="w.kind === 'next'" class="hc-muted" style="font-size:.7rem;"> next</span></td>
          <td v-if="isKit" class="num hc-num hc-muted">{{ kitOrders(w) }}</td>
          <td v-for="l in cols" :key="l.key" class="pc" :title="tip(cellOf(w, l.key))">
            <span v-if="mode === 'days'" class="hc-num" :style="l.key === 'pan' ? 'font-weight:600' : ''">{{ avgDays(cellOf(w, l.key)) == null ? '–' : `${avgDays(cellOf(w, l.key)).toFixed(1)}d` }}</span>
            <span v-else class="hc-pill" :class="cls(rate(cellOf(w, l.key)))">{{ rate(cellOf(w, l.key)) == null ? '–' : `${rate(cellOf(w, l.key)).toFixed(1)}%` }}</span><span v-if="delta(i, l.key)" class="hc-dd" :class="delta(i, l.key).cls">{{ delta(i, l.key).t }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
