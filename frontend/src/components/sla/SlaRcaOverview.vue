<script setup>
// RCA › Overview: on-time KPIs, the 5-week on-time trend, and city-wise late-delivery rates.
import { ref, computed } from "vue";
import { CITY_FILTERS, matchGroup, pct } from "./slaUtil.js";

const props = defineProps({ p: { type: Object, required: true } });
const o = computed(() => props.p.overview);

const cityFilter = ref("all");
const metric = ref("pct");
const cities = computed(() => o.value.cityRanking.filter(c => matchGroup(c.cityGroup, cityFilter.value)));
const maxCount = computed(() => Math.max(1, ...cities.value.map(c => c.count)));
const maxRate = computed(() => Math.max(1, ...cities.value.map(c => c.rate || 0)));

const trendMin = computed(() => {
  const v = o.value.weeklyTrend.map(w => w.onTimePct).filter(x => x != null);
  return v.length ? Math.max(0, Math.floor(Math.min(...v) / 10) * 10 - 10) : 0;
});
const barH = w => (w.onTimePct == null ? 0 : ((w.onTimePct - trendMin.value) / (100 - trendMin.value)) * 100);
const wowCls = computed(() => (o.value.wowChange == null ? "flat" : o.value.wowChange >= 0 ? "up-good" : "down-bad"));
</script>

<template>
  <div class="rca-kpis">
    <div class="rca-kpi" style="--k: var(--accent)">
      <div class="label">On-time · week {{ p.week }}</div>
      <div class="value">{{ pct(o.onTimePct) }}</div>
      <div class="sub">{{ o.onTime.toLocaleString("en-IN") }} of {{ o.delivered.toLocaleString("en-IN") }} delivered</div>
    </div>
    <div class="rca-kpi" style="--k: var(--sla-s1)">
      <div class="label">Week-over-week</div>
      <div class="value"><span class="delta" :class="wowCls" style="font-size:1.1rem; padding:2px 10px;">{{ o.wowChange == null ? "–" : `${o.wowChange >= 0 ? "▲" : "▼"} ${Math.abs(o.wowChange).toFixed(1)}pp` }}</span></div>
      <div class="sub">vs week {{ p.week - 1 }}</div>
    </div>
    <div class="rca-kpi" style="--k: var(--sla-s3)">
      <div class="label">5-week average</div>
      <div class="value">{{ pct(o.trendAvgPct) }}</div>
      <div class="sub">weeks {{ p.week - 4 }}–{{ p.week }}</div>
    </div>
    <div class="rca-kpi" style="--k: var(--sev-both)">
      <div class="label">Late deliveries</div>
      <div class="value">{{ p.rca.totalLate }}</div>
      <div class="sub">{{ p.rca.metroLate }} metro · {{ p.rca.otherLate }} other</div>
    </div>
  </div>

  <div class="sla-grid-2">
    <section class="sla-card">
      <div class="sla-card-head"><div><h3>Week-on-week on-time %</h3><p class="desc">Delivered on or before the promised date.</p></div></div>
      <div class="week-trend">
        <div v-for="w in o.weeklyTrend" :key="w.week" class="week-col" :class="{ cur: w.week === p.week }">
          <span class="pct">{{ w.onTimePct == null ? "No data" : pct(w.onTimePct) }}</span>
          <div class="bar" :style="{ height: barH(w) + '%' }" :title="`${w.onTime}/${w.total}`"></div>
          <span class="wk">W{{ w.week }}<span v-if="w.isPartial" class="chip chip-open" style="margin-left:4px; padding:0 5px;">live</span></span>
        </div>
      </div>
      <p class="mini-note">Bars start at {{ trendMin }}%, so the differences are easy to see. Hover a bar for on-time/total.</p>
    </section>

    <section class="sla-card">
      <div class="sla-card-head">
        <div><h3>City-wise late delivery</h3><p class="desc">Late ÷ delivered, per city, week {{ p.week }}.</p></div>
        <div class="tbl-tools">
          <select v-model="cityFilter"><option v-for="f in CITY_FILTERS" :key="f.id" :value="f.id">{{ f.label }}</option></select>
          <div class="seg sm"><button :class="{ active: metric === 'pct' }" @click="metric = 'pct'">%</button><button :class="{ active: metric === 'count' }" @click="metric = 'count'">late/total</button></div>
        </div>
      </div>
      <div class="bars scroll-y" style="max-height:300px; padding-right:4px;">
        <div v-for="c in cities" :key="c.city" class="barrow">
          <div class="barrow-top"><span>{{ c.city }} <span style="color:var(--muted); font-size:.74rem;">· {{ c.tier }}</span></span><span class="v">{{ metric === 'pct' ? pct(c.rate) : `${c.count}/${c.total}` }}</span></div>
          <div class="barrow-track"><div class="barrow-fill fill-both" :style="{ width: Math.max(3, metric === 'pct' ? (c.rate / maxRate) * 100 : (c.count / maxCount) * 100) + '%' }"></div></div>
        </div>
        <div v-if="!cities.length" class="empty-state">No late deliveries in this group.</div>
      </div>
    </section>
  </div>
</template>
