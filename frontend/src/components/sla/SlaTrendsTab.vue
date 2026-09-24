<script setup>
// SLA › Trends, modelled on /late-delivery-trends and reorganised around the three
// city tiers the business tracks:
//   Warehouse cities: Delhi, Bangalore, Kolkata, Mumbai, Hyderabad
//   MFC cities:       the RCA skill's 9-city list (see useSlaTrendsData.js)
//   Other cities:     everything else
// Three views, all under one Weekly/Monthly toggle:
//   1. SLA: avg days to deliver
//   2. Demand Share: 02a tier mix, 02b SDD share in the 5 WH cities, 02c SFX-MFC share,
//      02d Shadowfax share in other cities
//   3. On-Time Delivery
// Deltas always compare the last COMPLETE period with the one before it. The
// in-progress week or month is plotted, dashed, but never used as a headline number.
import { ref, computed } from "vue";
import SlaChart from "./SlaChart.vue";
import { useSlaTrendsData, WH_CITIES, MFC_CITIES, ratio } from "../../composables/useSlaTrendsData.js";

const props = defineProps({ rcaRun: { type: Object, default: null } });
const { weekly, monthly, loadError, lastSynced, rows } = useSlaTrendsData();

const grain = ref("week");
const view = ref("sla");
const VIEWS = [
  { id: "sla", label: "SLA" },
  { id: "demand", label: "Demand Share" },
  { id: "otd", label: "On-Time Delivery" },
];
const TIERS = [
  { key: "all", label: "Pan India", color: "--sla-pan", width: 3 },
  { key: "wh", label: "Warehouse cities", color: "--sla-s1" },
  { key: "mfc", label: "MFC cities", color: "--sla-s2" },
  { key: "other", label: "Other cities", color: "--sla-s3" },
];
const CITY_COLORS = ["--sla-s1", "--sla-s2", "--sla-s3", "--sla-s4", "--sla-s5"];

const P = computed(() => (grain.value === "week" ? weekly.value : monthly.value));
const labels = computed(() => P.value.map(p => p.label.short));
const titles = computed(() => P.value.map(p => p.label.long));
const partialLast = computed(() => !!P.value.at(-1)?.partial);
const unit = computed(() => (grain.value === "week" ? "WoW" : "MoM"));
// index of the last complete period, and the one before it
const iCur = computed(() => P.value.length - (partialLast.value ? 2 : 1));
const iPrev = computed(() => iCur.value - 1);

const tierOf = (p, k) => p.totals[k];
const cityOf = (p, k) => p.city[k] || { orders: 0, tat_sum: 0, tat_n: 0, on_time: 0, ds_facility_orders: 0, sdd_lsp_orders: 0, sfx_mfc_orders: 0, sfx_orders: 0 };

const slaSeries = computed(() => TIERS.map(t => ({ ...t, data: P.value.map(p => ratio(tierOf(p, t.key).tat_sum, tierOf(p, t.key).tat_n)) })));
const otdSeries = computed(() => TIERS.map(t => ({ ...t, data: P.value.map(p => ratio(tierOf(p, t.key).on_time, tierOf(p, t.key).orders)) })));
const mixSeries = computed(() => TIERS.slice(1).map(t => ({ ...t, data: P.value.map(p => ratio(tierOf(p, t.key).orders, p.totals.all.orders)) })));
const sddDemandSeries = computed(() => WH_CITIES.map((c, i) => ({
  label: c.label, color: CITY_COLORS[i], data: P.value.map(p => ratio(cityOf(p, c.key).ds_facility_orders, cityOf(p, c.key).orders)),
})));
const otherSfxSeries = computed(() => [{
  label: "Shadowfax share · Other cities", color: "--sla-s3", width: 3,
  data: P.value.map(p => ratio(p.totals.other.sfx_orders, p.totals.other.orders)),
}]);
// MFC cities x periods, SFX-MFC share. A heatmap rather than 9 lines: nine series is
// past the categorical palette's limit, and every value is 0 until the MFCs go live anyway.
const mfcMatrix = computed(() => MFC_CITIES.map(c => ({
  ...c, cells: P.value.map(p => { const x = cityOf(p, c.key); return { v: ratio(x.sfx_mfc_orders, x.orders), n: x.sfx_mfc_orders, d: x.orders }; }),
})));
const mfcLive = computed(() => mfcMatrix.value.some(r => r.cells.some(c => c.n > 0)));

// ---- deltas ----
function deltaCells(series, fmt, higherIsGood) {
  return series.map(s => {
    const cur = s.data[iCur.value], prev = s.data[iPrev.value];
    const d = cur != null && prev != null ? cur - prev : null;
    return { label: s.label, color: s.color, value: fmt(cur), delta: d, deltaText: deltaText(d, fmt === fmtDays), cls: deltaCls(d, higherIsGood) };
  });
}
const fmtPct = v => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
const fmtDays = v => (v == null ? "–" : `${v.toFixed(2)}d`);
function deltaText(d, days) {
  if (d == null) return "–";
  if (Math.abs(d) < (days ? 0.005 : 0.0005)) return "flat";
  const arrow = d > 0 ? "▲" : "▼";
  return days ? `${arrow} ${Math.abs(d).toFixed(2)}d` : `${arrow} ${Math.abs(d * 100).toFixed(1)}pp`;
}
function deltaCls(d, higherIsGood) {
  if (d == null || Math.abs(d) < 0.0005) return "flat";
  return d > 0 ? (higherIsGood ? "up-good" : "up-bad") : (higherIsGood ? "down-bad" : "down-good");
}
const curLabel = computed(() => P.value[iCur.value]?.label.long || "–");
const prevLabel = computed(() => P.value[iPrev.value]?.label.short || "–");

// ---- hero KPIs ----
const hero = computed(() => {
  const cur = P.value[iCur.value], prev = P.value[iPrev.value];
  if (!cur) return [];
  const sla = p => p && ratio(p.totals.all.tat_sum, p.totals.all.tat_n);
  const otd = p => p && ratio(p.totals.all.on_time, p.totals.all.orders);
  const sdd = p => p && ratio(p.totals.wh.ds_facility_orders, p.totals.wh.orders); // volume-weighted across the 5 cities
  const d = (f) => (f(cur) != null && f(prev) != null ? f(cur) - f(prev) : null);
  const open = props.rcaRun?.payload?.openSla?.summary;
  return [
    { label: "Pan India SLA", value: fmtDays(sla(cur)), delta: deltaText(d(sla), true), cls: deltaCls(d(sla), false), sub: `avg days to deliver` },
    { label: "On-time delivery", value: fmtPct(otd(cur)), delta: deltaText(d(otd), false), cls: deltaCls(d(otd), true), sub: `${cur.totals.all.on_time.toLocaleString("en-IN")} / ${cur.totals.all.orders.toLocaleString("en-IN")} orders` },
    { label: "SDD demand share", value: fmtPct(sdd(cur)), delta: deltaText(d(sdd), false), cls: deltaCls(d(sdd), true), sub: "5 warehouse cities, weighted" },
    { label: "Open SLA breaches", value: open ? String(open.total) : "–", delta: null, cls: "flat", sub: open ? `oldest ${open.oldestBreach}d · ${props.rcaRun.payload.openSla.weekLabel}` : "awaiting first RCA sync" },
  ];
});

const staleHours = computed(() => (lastSynced.value ? (Date.now() - new Date(lastSynced.value).getTime()) / 3600000 : null));
const freshText = computed(() => {
  if (!lastSynced.value) return "not synced yet";
  const d = new Date(lastSynced.value);
  return `synced ${d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`;
});
const tileStyle = v => {
  if (v == null) return {};
  const a = Math.min(1, v / 0.4); // 40%+ share = full tint
  return v > 0 ? { background: `color-mix(in srgb, var(--sla-s2) ${Math.round(12 + a * 70)}%, var(--surface))`, color: a > 0.55 ? "#fff" : "var(--ink)" } : {};
};
</script>

<template>
  <div v-if="loadError" class="form-error">Couldn't load SLA trends: {{ loadError }}</div>

  <section class="sla-hero">
    <div class="sla-hero-top">
      <div>
        <div class="sla-eyebrow">SLA · Leadership view · {{ grain === 'week' ? 'Weekly' : 'Monthly' }}</div>
        <h2>{{ curLabel }} <span style="opacity:.6; font-weight:500; font-size:.9rem;">vs {{ prevLabel }}</span></h2>
      </div>
      <span class="fresh" :class="{ stale: staleHours != null && staleHours > 30 }">{{ freshText }}</span>
    </div>
    <div class="sla-hero-kpis">
      <div v-for="k in hero" :key="k.label" class="sla-hero-kpi">
        <div class="label">{{ k.label }}</div>
        <div class="value">{{ k.value }}</div>
        <div class="sub"><span v-if="k.delta" class="delta" :class="k.cls">{{ k.delta }} {{ unit }}</span> {{ k.sub }}</div>
      </div>
    </div>
  </section>

  <div v-if="!rows.length && !loadError" class="sla-card empty-state">No trend data yet. It appears after the first SLA sync runs.</div>

  <template v-else>
    <div class="sla-controls">
      <div class="sla-views">
        <button v-for="(v, i) in VIEWS" :key="v.id" class="sla-view-pill" :class="{ active: view === v.id }" @click="view = v.id">
          <span class="ix">0{{ i + 1 }}</span>{{ v.label }}
        </button>
      </div>
      <div class="seg">
        <button :class="{ active: grain === 'week' }" @click="grain = 'week'">Weekly</button>
        <button :class="{ active: grain === 'month' }" @click="grain = 'month'">Monthly</button>
      </div>
    </div>

    <!-- 01 SLA -->
    <section v-if="view === 'sla'" class="sla-card">
      <div class="sla-card-head">
        <div>
          <div class="sla-card-step">01 · SLA</div>
          <h3>Average days to deliver</h3>
          <p class="desc">Order placed to delivered, in calendar days, bucketed by promised-delivery {{ grain }}. Lower is better.</p>
        </div>
      </div>
      <SlaChart :labels="labels" :tooltip-titles="titles" :datasets="slaSeries" format="days" :partial-last="partialLast" />
      <div class="sla-deltas">
        <div v-for="c in deltaCells(slaSeries, fmtDays, false)" :key="c.label" class="sla-delta-cell">
          <span class="name"><span class="swatch" :style="{ background: `var(${c.color})` }"></span>{{ c.label }}</span>
          <span class="row"><span class="v">{{ c.value }}</span><span class="delta" :class="c.cls">{{ c.deltaText }}</span></span>
        </div>
      </div>
    </section>

    <!-- 02 Demand share -->
    <template v-if="view === 'demand'">
      <section class="sla-card">
        <div class="sla-card-head">
          <div>
            <div class="sla-card-step">02a · Demand share</div>
            <h3>Where orders come from</h3>
            <p class="desc">Share of pan-India delivered orders from warehouse, MFC and other cities.</p>
          </div>
        </div>
        <SlaChart type="bar" stacked :labels="labels" :tooltip-titles="titles" :datasets="mixSeries" format="pct" :y-min="0" :y-max="1" :partial-last="partialLast" />
        <div class="sla-deltas">
          <div v-for="c in deltaCells(mixSeries, fmtPct, true)" :key="c.label" class="sla-delta-cell">
            <span class="name"><span class="swatch" :style="{ background: `var(${c.color})` }"></span>{{ c.label }}</span>
            <span class="row"><span class="v">{{ c.value }}</span><span class="delta flat">{{ c.deltaText }}</span></span>
          </div>
        </div>
      </section>

      <section class="sla-card">
        <div class="sla-card-head">
          <div>
            <div class="sla-card-step">02b · SDD demand share</div>
            <h3>Same-day demand in the 5 SDD cities</h3>
            <p class="desc">Share of each city's orders shipped from one of its own dark stores rather than the mother warehouse.</p>
          </div>
        </div>
        <SlaChart :labels="labels" :tooltip-titles="titles" :datasets="sddDemandSeries" format="pct" :y-min="0" :partial-last="partialLast" />
        <div class="sla-deltas">
          <div v-for="c in deltaCells(sddDemandSeries, fmtPct, true)" :key="c.label" class="sla-delta-cell">
            <span class="name"><span class="swatch" :style="{ background: `var(${c.color})` }"></span>{{ c.label }}</span>
            <span class="row"><span class="v">{{ c.value }}</span><span class="delta" :class="c.cls">{{ c.deltaText }}</span></span>
          </div>
        </div>
      </section>

      <section class="sla-card">
        <div class="sla-card-head">
          <div>
            <div class="sla-card-step">02c · MFC demand share</div>
            <h3>SFX MFC demand share</h3>
            <p class="desc">Share of each MFC city's orders shipped from a Shadowfax MFC (PB-UC-SFX-* facilities).</p>
          </div>
        </div>
        <div v-if="!mfcLive" class="sla-notlive"><span class="chip chip-muted">Not live yet</span><span>The SFX MFCs haven't shipped their first order, so every city reads <strong>0%</strong>. This view fills in automatically once they start.</span></div>
        <div class="table-scroll">
          <table class="sla-heat">
            <thead><tr><th class="city">City</th><th v-for="(l, i) in labels" :key="i">{{ l }}</th></tr></thead>
            <tbody>
              <tr v-for="r in mfcMatrix" :key="r.key">
                <td class="city">{{ r.label }}</td>
                <td v-for="(c, i) in r.cells" :key="i"><span class="tile" :style="tileStyle(c.v)" :title="`${r.label} · ${titles[i]}: ${c.n}/${c.d} orders`">{{ c.d ? fmtPct(c.v) : '–' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="sla-card">
        <div class="sla-card-head">
          <div>
            <div class="sla-card-step">02d · Other cities SFX share</div>
            <h3>Shadowfax share · other cities (combined)</h3>
            <p class="desc">Share of all other-city orders carried by any Shadowfax service.</p>
          </div>
        </div>
        <SlaChart :labels="labels" :tooltip-titles="titles" :datasets="otherSfxSeries" format="pct" :y-min="0" :partial-last="partialLast" />
        <div class="sla-deltas">
          <div v-for="c in deltaCells(otherSfxSeries, fmtPct, true)" :key="c.label" class="sla-delta-cell">
            <span class="name"><span class="swatch" :style="{ background: `var(${c.color})` }"></span>{{ c.label }}</span>
            <span class="row"><span class="v">{{ c.value }}</span><span class="delta" :class="c.cls">{{ c.deltaText }}</span></span>
          </div>
        </div>
      </section>
    </template>

    <!-- 03 OTD -->
    <section v-if="view === 'otd'" class="sla-card">
      <div class="sla-card-head">
        <div>
          <div class="sla-card-step">03 · On-time delivery</div>
          <h3>On-time delivery rate</h3>
          <p class="desc">Delivered on or before the promised date, as a share of delivered orders. Higher is better.</p>
        </div>
      </div>
      <SlaChart :labels="labels" :tooltip-titles="titles" :datasets="otdSeries" format="pct" :partial-last="partialLast" />
      <div class="sla-deltas">
        <div v-for="c in deltaCells(otdSeries, fmtPct, true)" :key="c.label" class="sla-delta-cell">
          <span class="name"><span class="swatch" :style="{ background: `var(${c.color})` }"></span>{{ c.label }}</span>
          <span class="row"><span class="v">{{ c.value }}</span><span class="delta" :class="c.cls">{{ c.deltaText }}</span></span>
        </div>
      </div>
    </section>

    <p class="sla-foot">
      Deltas compare {{ curLabel }} with {{ prevLabel }}, the last two complete {{ grain === 'week' ? 'weeks' : 'months' }}. The dashed final segment is the {{ grain }} still in progress.
      Source: Jarvis (RO On Time RCA base), delivered net D2C orders on the UC channel, bucketed by promised-delivery week (Monday start).
    </p>
  </template>
</template>
