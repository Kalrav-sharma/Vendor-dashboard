<script setup>
// Health Card › SLA & Demand Share: two tables side by side, driven by one RO | Locks toggle.
// Rows: next week (once it starts), the current week, then the 4 previous weeks.
import { ref, computed } from "vue";
import { TIERS } from "../../composables/useHealthSlaData.js";

const props = defineProps({
  ro: { type: Array, required: true },
  locks: { type: Array, required: true },
});
const product = ref("ro");
const weeks = computed(() => (product.value === "ro" ? props.ro : props.locks));
const SHARE_TIERS = TIERS.filter(t => t.key !== "pan");

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const range = ws => {
  const s = new Date(`${ws}T00:00:00Z`), e = new Date(s.getTime() + 6 * 86400000);
  return `${s.getUTCDate()} ${MONTHS[s.getUTCMonth()]} – ${e.getUTCDate()} ${MONTHS[e.getUTCMonth()]}`;
};
const days = v => (v == null ? "–" : `${v.toFixed(1)}d`);
const pct = v => (v == null ? "–" : `${(v * 100).toFixed(0)}%`);
// Arrow compares the values as displayed (1 dp), so equal-looking weeks show no arrow.
const r1 = v => (v == null ? null : Math.round(v * 10) / 10);
function delta(i, tier) {
  const cur = r1(weeks.value[i]?.tiers[tier].sla), prev = r1(weeks.value[i + 1]?.tiers[tier].sla);
  if (cur == null || prev == null || cur === prev) return null;
  return { cls: cur > prev ? "up" : "down", t: cur > prev ? "▲" : "▼" };
}
const shortTier = t => ({ pan: "Pan India", top5: "Top 5", next4: "Next 4", other: "Other" }[t.key]);
</script>

<template>
  <div class="hc-view">
    <h3 class="hc-group-title">
      SLA &amp; Demand Share
      <span class="hc-toggle">
        <button :class="{ active: product === 'ro' }" @click="product = 'ro'">RO</button>
        <button :class="{ active: product === 'locks' }" @click="product = 'locks'">Locks</button>
      </span>
    </h3>
    <div class="hc-pair">
      <section class="table-card">
        <h3 class="card-caption">SLA</h3>
        <table class="hc-table">
          <thead><tr><th>Week</th><th v-for="t in TIERS" :key="t.key" class="num" :title="t.hint">{{ shortTier(t) }}</th></tr></thead>
          <tbody>
            <tr v-for="(w, i) in weeks" :key="w.weekStart" :title="range(w.weekStart)">
              <td class="lab">W{{ w.weekNo ?? '–' }}<span v-if="w.kind === 'current'" class="hc-live"></span><span v-if="w.kind === 'next'" class="hc-muted" style="font-size:.7rem;"> next</span></td>
              <td v-for="t in TIERS" :key="t.key" class="num hc-num" :style="t.key === 'pan' ? 'font-weight:600' : ''">
                {{ days(w.tiers[t.key].sla) }}<span v-if="delta(i, t.key)" class="hc-dd" :class="delta(i, t.key).cls">{{ delta(i, t.key).t }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
      <section class="table-card">
        <h3 class="card-caption">Demand Share</h3>
        <table class="hc-table">
          <thead><tr><th>Week</th><th class="num">Orders</th><th v-for="t in SHARE_TIERS" :key="t.key" class="num" :title="t.hint">{{ shortTier(t) }}</th></tr></thead>
          <tbody>
            <tr v-for="w in weeks" :key="w.weekStart" :title="range(w.weekStart)">
              <td class="lab">W{{ w.weekNo ?? '–' }}<span v-if="w.kind === 'current'" class="hc-live"></span><span v-if="w.kind === 'next'" class="hc-muted" style="font-size:.7rem;"> next</span></td>
              <td class="num hc-num hc-muted">{{ w.tiers.pan.orders.toLocaleString('en-IN') }}</td>
              <td v-for="t in SHARE_TIERS" :key="t.key" class="num hc-num">{{ pct(w.tiers[t.key].share) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </div>
</template>
