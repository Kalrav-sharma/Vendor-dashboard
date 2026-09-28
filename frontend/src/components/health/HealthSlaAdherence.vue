<script setup>
// Health Card › SLA Adherence per week, split by the carrying LSP: Pan India, DTDC Raftaar,
// SFX dark store, Other. Days | % toggle: average SLA (ACTUAL_TAT days) or on-time %
// (delivered by promise date). RO | Locks toggle.
// Rows as in SLA & Demand Share: next week (once it starts), current week, 4 previous.
import { ref, computed } from "vue";
import { ADH_LSPS } from "../../composables/useHealthSlaData.js";

const props = defineProps({
  ro: { type: Array, required: true },
  locks: { type: Array, required: true },
});
const product = ref("ro");
const mode = ref("days");
const weeks = computed(() => (product.value === "ro" ? props.ro : props.locks));

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
  const cur = val(weeks.value[i]?.adh[key]), prev = val(weeks.value[i + 1]?.adh[key]);
  if (cur == null || prev == null || cur === prev) return null;
  const better = mode.value === "days" ? cur < prev : cur > prev;
  return { cls: better ? "good" : "bad", t: cur > prev ? "▲" : "▼" };
}
const tip = c => (c && c.total ? `${c.onTime.toLocaleString("en-IN")} / ${c.total.toLocaleString("en-IN")} on time` : "No orders");
</script>

<template>
  <section class="table-card hc-view">
    <div class="card-caption hc-caption">
      <span>SLA Adherence</span>
      <span>
        <span class="hc-toggle" style="margin-right:6px;">
          <button :class="{ active: mode === 'days' }" @click="mode = 'days'">Days</button>
          <button :class="{ active: mode === 'pct' }" @click="mode = 'pct'">%</button>
        </span>
        <span class="hc-toggle">
          <button :class="{ active: product === 'ro' }" @click="product = 'ro'">RO</button>
          <button :class="{ active: product === 'locks' }" @click="product = 'locks'">Locks</button>
        </span>
      </span>
    </div>
    <table class="hc-table">
      <colgroup><col style="width:16%"><col><col><col><col></colgroup>
      <thead><tr><th>Week</th><th v-for="l in ADH_LSPS" :key="l.key" class="c" :title="l.hint">{{ l.label }}</th></tr></thead>
      <tbody>
        <tr v-for="(w, i) in weeks" :key="w.weekStart" :title="range(w.weekStart)">
          <td class="lab">W{{ w.weekNo ?? '–' }}<span v-if="w.kind === 'current'" class="hc-live"></span><span v-if="w.kind === 'next'" class="hc-muted" style="font-size:.7rem;"> next</span></td>
          <td v-for="l in ADH_LSPS" :key="l.key" class="pc" :title="tip(w.adh[l.key])">
            <span v-if="mode === 'days'" class="hc-num" :style="l.key === 'pan' ? 'font-weight:600' : ''">{{ avgDays(w.adh[l.key]) == null ? '–' : `${avgDays(w.adh[l.key]).toFixed(1)}d` }}</span>
            <span v-else class="hc-pill" :class="cls(rate(w.adh[l.key]))">{{ rate(w.adh[l.key]) == null ? '–' : `${rate(w.adh[l.key]).toFixed(1)}%` }}</span><span v-if="delta(i, l.key)" class="hc-dd" :class="delta(i, l.key).cls">{{ delta(i, l.key).t }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
