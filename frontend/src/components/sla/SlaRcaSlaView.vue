<script setup>
// RCA › SLA View: the skill's seven cards, in the same order.
//   1 Week-on-week SLA  2 Demand share  3 City-wise SLA  4 City × LSP share
//   5 City × LSP on-time  6 Serviceability coverage
// (The skill's 7th card, Ideal SLA coverage, is deliberately not shown on the portal.)
// "SLA" means average ACTUAL_TAT in days, not a compliance percentage, matching the skill.
import { ref, computed } from "vue";
import { CITY_FILTERS, matchGroup, pct, days, otdCls } from "./slaUtil.js";

const props = defineProps({ p: { type: Object, required: true } });
const v = computed(() => props.p.slaView);

// share-cell tint: accent, stronger with share
const tint = share => (share ? { background: `color-mix(in srgb, var(--accent) ${Math.round(6 + share * 55)}%, var(--surface))`, color: share > 0.55 ? "#fff" : "var(--ink)" } : {});

// 2 demand share
const dsMetric = ref("pct");
const DS_COLORS = { wh: "--sla-s1", mfc: "--sla-s2", other: "--sla-s3" };

// 3 city-wise SLA
const f3 = ref("all");
const citySla = computed(() => v.value.cityAvgSla.cities.filter(c => matchGroup(c.cityGroup, f3.value)));

// 4 city × LSP share
const f4 = ref("Top9");
const lspShare = computed(() => v.value.cityLspShare.cities.filter(c => matchGroup(c.cityGroup, f4.value)));
const lspShareCols = computed(() => v.value.cityLspShare.lsps.filter(l => lspShare.value.some(c => c.counts[l])));

// 5 city × LSP OTD
const f5 = ref("Top9");
const f5Lsp = ref("all");
const m5 = ref("rate");
const otdCols = computed(() => {
  const all = v.value.cityLspOtd.lsps;
  return f5Lsp.value === "all" ? all.filter(l => otdRows.value.some(c => c.cells[l])) : [f5Lsp.value];
});
const otdRows = computed(() => v.value.cityLspOtd.cities.filter(c => matchGroup(c.cityGroup, f5.value)));
const otdText = cell => (!cell || !cell.total ? "–" : m5.value === "rate" ? pct((cell.onTime / cell.total) * 100) : `${cell.onTime}/${cell.total}`);
const otdClass = cell => (!cell || !cell.total ? "" : otdCls((cell.onTime / cell.total) * 100));

// 6 serviceability
const f6 = ref("Top9");
const f6Lsp = ref("all");
const svc = computed(() => v.value.serviceability);
const svcRows = computed(() => {
  if (!svc.value) return [];
  return svc.value.cities.filter(c => matchGroup(c.cityGroup, f6.value)).map(c => {
    const src = f6Lsp.value === "all" ? c : (c.byLsp?.[f6Lsp.value] || { counts: {}, total: 0 });
    return { city: c.city, counts: src.counts, total: src.total };
  }).filter(c => c.total > 0);
});
const svcTotals = computed(() => {
  const t = {}; let g = 0;
  svcRows.value.forEach(c => { Object.entries(c.counts).forEach(([k, n]) => { t[k] = (t[k] || 0) + n; }); g += c.total; });
  return { t, g };
});

</script>

<template>
  <!-- 1 -->
  <section class="sla-card">
    <div class="sla-card-head"><div><div class="sla-card-step">01</div><h3>Week-on-week SLA</h3><p class="desc">Average days to deliver, by promised-delivery week.</p></div></div>
    <div class="table-scroll">
      <table style="min-width:720px;">
        <thead><tr><th>Week</th><th class="num">Average</th><th class="num">Warehouse cities</th><th class="num">Raftaar cities</th><th class="num">SFX_NDD cities</th><th class="num">MFC cities</th><th class="num">Other cities</th></tr></thead>
        <tbody>
          <tr v-for="w in v.weeklySla" :key="w.week" :class="{ 'row-total': w.week === p.week }">
            <td class="mono">Week {{ w.week }}<span v-if="w.week === p.week" class="chip chip-info" style="margin-left:6px;">this RCA</span></td>
            <td class="num mono">{{ days(w.avgSla) }}</td><td class="num mono">{{ days(w.whSla) }}</td><td class="num mono">{{ days(w.raftaarSla) }}</td>
            <td class="num mono">{{ days(w.sfxNddSla) }}</td><td class="num mono">{{ days(w.mfcSla) }}</td><td class="num mono">{{ days(w.otherSla) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <div class="sla-grid-2">
    <!-- 2 -->
    <section class="sla-card">
      <div class="sla-card-head">
        <div><div class="sla-card-step">02</div><h3>Demand share · week {{ p.week }}</h3><p class="desc">{{ v.demandShare.total.toLocaleString("en-IN") }} delivered orders.</p></div>
        <div class="seg sm"><button :class="{ active: dsMetric === 'pct' }" @click="dsMetric = 'pct'">%</button><button :class="{ active: dsMetric === 'count' }" @click="dsMetric = 'count'">#</button></div>
      </div>
      <div style="display:flex; height:14px; border-radius:7px; overflow:hidden; gap:2px; margin-bottom:14px;">
        <div v-for="g in v.demandShare.groups" :key="g.key" :style="{ flex: g.count || 0.0001, background: `var(${DS_COLORS[g.key]})` }" :title="`${g.label}: ${g.pct.toFixed(1)}%`"></div>
      </div>
      <div class="bars">
        <div v-for="g in v.demandShare.groups" :key="g.key" class="barrow">
          <div class="barrow-top"><span><span class="swatch" :style="{ display:'inline-block', width:'10px', height:'10px', borderRadius:'3px', marginRight:'6px', background: `var(${DS_COLORS[g.key]})` }"></span>{{ g.label }}</span><span class="v">{{ dsMetric === 'pct' ? pct(g.pct) : g.count.toLocaleString("en-IN") }}</span></div>
        </div>
      </div>
    </section>

    <!-- 3 -->
    <section class="sla-card">
      <div class="sla-card-head">
        <div><div class="sla-card-step">03</div><h3>City-wise SLA · week {{ p.week }}</h3></div>
        <div class="tbl-tools"><select v-model="f3"><option v-for="f in CITY_FILTERS" :key="f.id" :value="f.id">{{ f.label }}</option></select><span class="count">{{ citySla.length }} cities</span></div>
      </div>
      <div class="table-scroll scroll-y" style="max-height:300px;">
        <table style="min-width:0;">
          <thead><tr><th>City</th><th class="num">Average SLA</th><th class="num">Orders</th></tr></thead>
          <tbody><tr v-for="c in citySla" :key="c.city"><td>{{ c.city }}</td><td class="num mono">{{ days(c.avgSla) }}</td><td class="num mono">{{ c.total }}</td></tr></tbody>
        </table>
      </div>
    </section>
  </div>

  <!-- 4 -->
  <section class="table-card" style="margin-bottom:16px;">
    <div class="sla-card-head" style="padding:14px 16px 0;">
      <div><div class="sla-card-step">04</div><h3>City × LSP share · week {{ p.week }}</h3><p class="desc">Share of each city's delivered orders by LSP group.</p></div>
      <div class="tbl-tools"><select v-model="f4"><option v-for="f in CITY_FILTERS" :key="f.id" :value="f.id">{{ f.label }}</option></select><span class="count">{{ lspShare.length }} cities</span></div>
    </div>
    <div class="table-scroll scroll-y" style="margin-top:12px;">
      <table>
        <thead><tr><th>City</th><th v-for="l in lspShareCols" :key="l" class="num">{{ l }}</th><th class="num">Orders</th></tr></thead>
        <tbody>
          <tr v-for="c in lspShare" :key="c.city">
            <td>{{ c.city }}</td>
            <td v-for="l in lspShareCols" :key="l" class="num mono heat-cell" :style="tint(c.counts[l] ? c.counts[l] / c.total : 0)">{{ c.counts[l] ? pct((c.counts[l] / c.total) * 100) : "–" }}</td>
            <td class="num mono"><strong>{{ c.total }}</strong></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <!-- 5 -->
  <section class="table-card" style="margin-bottom:16px;">
    <div class="sla-card-head" style="padding:14px 16px 0;">
      <div><div class="sla-card-step">05</div><h3>City × LSP on-time delivery · week {{ p.week }}</h3><p class="desc">Red below 70%, green at 90% and above. Smart-locks, Porter and self-pickup are excluded.</p></div>
      <div class="tbl-tools">
        <select v-model="f5"><option v-for="f in CITY_FILTERS" :key="f.id" :value="f.id">{{ f.label }}</option></select>
        <select v-model="f5Lsp"><option value="all">All LSPs</option><option v-for="l in v.cityLspOtd.lsps" :key="l" :value="l">{{ l }}</option></select>
        <div class="seg sm"><button :class="{ active: m5 === 'rate' }" @click="m5 = 'rate'">%</button><button :class="{ active: m5 === 'count' }" @click="m5 = 'count'">#</button></div>
      </div>
    </div>
    <div class="table-scroll scroll-y" style="margin-top:12px;">
      <table>
        <thead><tr><th>City</th><th v-for="l in otdCols" :key="l" class="num">{{ l }}</th><th class="num">Total</th></tr></thead>
        <tbody>
          <tr v-for="c in otdRows" :key="c.city">
            <td>{{ c.city }}</td>
            <td v-for="l in otdCols" :key="l" class="num mono" :class="otdClass(c.cells[l])">{{ otdText(c.cells[l]) }}</td>
            <td class="num mono" :class="otdClass(c.total)"><strong>{{ otdText(c.total) }}</strong></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <!-- 6 -->
  <section class="table-card" style="margin-bottom:16px;">
    <div class="sla-card-head" style="padding:14px 16px 0;">
      <div><div class="sla-card-step">06</div><h3>Serviceability coverage · city × SLA code</h3><p class="desc">Active serviceability rules, live from the serviceability rulebook.</p></div>
      <div v-if="svc" class="tbl-tools">
        <select v-model="f6"><option v-for="f in CITY_FILTERS" :key="f.id" :value="f.id">{{ f.label }}</option></select>
        <select v-model="f6Lsp"><option value="all">All partners</option><option v-for="l in svc.lspValues" :key="l" :value="l">{{ l }}</option></select>
        <span class="count">{{ svcRows.length }} cities</span>
      </div>
    </div>
    <div v-if="!svc" class="empty-state">Serviceability data unavailable this run.</div>
    <div v-else class="table-scroll scroll-y" style="margin-top:12px;">
      <table>
        <thead><tr><th>City</th><th v-for="code in svc.slaCodes" :key="code" class="num">{{ code }}</th><th class="num">Total</th></tr></thead>
        <tbody>
          <tr v-for="c in svcRows" :key="c.city">
            <td>{{ c.city }}</td>
            <td v-for="code in svc.slaCodes" :key="code" class="num mono heat-cell" :style="tint(c.counts[code] ? c.counts[code] / c.total : 0)">{{ c.counts[code] || 0 }}</td>
            <td class="num mono"><strong>{{ c.total }}</strong></td>
          </tr>
          <tr class="row-total"><td>Total</td><td v-for="code in svc.slaCodes" :key="code" class="num mono">{{ svcTotals.t[code] || 0 }}</td><td class="num mono">{{ svcTotals.g }}</td></tr>
        </tbody>
      </table>
    </div>
  </section>

</template>
