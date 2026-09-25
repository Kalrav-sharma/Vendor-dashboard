<script setup>
// Health Card › 02 SLA & Demand Share. One table per product (RO / Locks toggle).
// Rows: next week (once it has orders), the current week, then the 4 previous weeks.
// Columns: Pan India, Top 5, Next 4 and Other cities, each with SLA (avg days to deliver)
// and Demand Share (the tier's share of Pan India orders).
import { ref, computed } from "vue";
import { TIERS } from "../../composables/useHealthSlaData.js";

const props = defineProps({
  ro: { type: Array, required: true },
  locks: { type: Array, required: true },
});
const product = ref("ro");
const weeks = computed(() => (product.value === "ro" ? props.ro : props.locks));

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const range = ws => {
  const s = new Date(`${ws}T00:00:00Z`), e = new Date(s.getTime() + 6 * 86400000);
  return `${s.getUTCDate()} ${MONTHS[s.getUTCMonth()]} – ${e.getUTCDate()} ${MONTHS[e.getUTCMonth()]}`;
};
const days = v => (v == null ? "–" : `${v.toFixed(2)}d`);
const pct = v => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);

// change vs the week below (the previous one): up = slower = bad
function delta(i, tier) {
  const cur = weeks.value[i]?.tiers[tier].sla, prev = weeks.value[i + 1]?.tiers[tier].sla;
  if (cur == null || prev == null) return null;
  const d = cur - prev;
  if (Math.abs(d) < 0.005) return { cls: "flat", t: "flat" };
  return { cls: d > 0 ? "up" : "down", t: `${d > 0 ? "▲" : "▼"} ${Math.abs(d).toFixed(2)}` };
}
// a tier noticeably slower or faster than Pan India that week
function speed(w, tier) {
  if (tier === "pan") return "";
  const s = w.tiers[tier].sla, p = w.tiers.pan.sla;
  if (s == null || p == null) return "";
  return s > p * 1.25 ? "slow" : s < p * 0.8 ? "fast" : "";
}
const totalOrders = list => list.reduce((s, w) => s + w.tiers.pan.orders, 0);
</script>

<template>
  <div class="hc-view-head">
    <div>
      <div class="hc-step">02 · SLA &amp; Demand Share</div>
      <h3>Delivery speed and where demand comes from</h3>
      <p class="desc">SLA is the average number of days from order to delivery (lower is better). Demand share is each tier's share of Pan India delivered orders. Weeks are promised-delivery weeks.</p>
    </div>
    <div class="prod-toggle">
      <button :class="{ active: product === 'ro' }" @click="product = 'ro'">RO <span class="n">{{ totalOrders(ro).toLocaleString('en-IN') }}</span></button>
      <button :class="{ active: product === 'locks' }" @click="product = 'locks'">Locks <span class="n">{{ totalOrders(locks).toLocaleString('en-IN') }}</span></button>
    </div>
  </div>

  <section class="hc-card">
    <div v-if="!weeks.length || !totalOrders(weeks)" class="empty-state">No {{ product === 'ro' ? 'RO' : 'Locks' }} delivery data yet. It appears after the next SLA sync.</div>
    <div v-else class="table-scroll">
      <table class="sd-table">
        <thead>
          <tr class="grp">
            <th rowspan="2" style="text-align:left; vertical-align:bottom; padding-bottom:9px;">Week</th>
            <th v-for="t in TIERS" :key="t.key" colspan="2">{{ t.label }}<small v-if="t.hint">{{ t.hint }}</small><small v-else>all cities</small></th>
          </tr>
          <tr class="sub">
            <template v-for="t in TIERS" :key="t.key">
              <th class="l">SLA</th><th>{{ t.key === 'pan' ? 'Orders' : 'Demand share' }}</th>
            </template>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(w, i) in weeks" :key="w.weekStart" :class="w.kind">
            <td class="wk">
              <span class="w">Week {{ w.weekNo ?? '–' }}</span>
              <span v-if="w.kind === 'current'" class="tag live">in progress</span>
              <span v-if="w.kind === 'next'" class="tag early">early</span>
              <span class="r">{{ range(w.weekStart) }}</span>
            </td>
            <template v-for="t in TIERS" :key="t.key">
              <td class="l">
                <span class="sla-v" :class="speed(w, t.key)">{{ days(w.tiers[t.key].sla) }}</span>
                <span v-if="delta(i, t.key)" class="dd" :class="delta(i, t.key).cls">{{ delta(i, t.key).t }}</span>
              </td>
              <td>
                <template v-if="t.key === 'pan'">{{ w.tiers.pan.orders.toLocaleString('en-IN') }}</template>
                <span v-else class="share">{{ pct(w.tiers[t.key].share) }}<span class="bar"><span :style="{ width: ((w.tiers[t.key].share || 0) * 100) + '%' }"></span></span></span>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
  <p class="hc-foot">
    ▲/▼ is the change in days vs the week below; ▲ (slower) is red. An SLA figure in orange runs 25%+ slower than Pan India that week; one in green runs 20%+ faster.
    The current and next weeks are still maturing, because orders promised for them are still being delivered.
    Locks = all UC-channel D2C orders that aren't RO, the same base as the Locks RCA.
  </p>
</template>
