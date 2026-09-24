<script setup>
// RCA › RCA Breakdown: the cause mix, the deep-dive insights (dispatch, transit plus
// docket-level LSP classification, no-data), and the full late-order detail table.
// Every figure comes from the payload. Nothing is recomputed here.
import { ref, computed } from "vue";
import { SEV, CLASS_LABEL, CLASS_SEV, dash, downloadCsv } from "./slaUtil.js";

const props = defineProps({ p: { type: Object, required: true } });
const r = computed(() => props.p.rca);
const dd = computed(() => r.value.deepDive);

// ---- donut ----
const R = 70, C = 2 * Math.PI * R;
const arcs = computed(() => {
  const total = r.value.totalLate || 1;
  let offset = 0;
  return r.value.rcaBreakdown.filter(c => c.count > 0).map(c => {
    const len = (c.count / total) * C;
    const a = { ...c, dash: `${Math.max(0, len - 2)} ${C}`, offset: -offset, sev: SEV[c.category] };
    offset += len;
    return a;
  });
});
const maxCat = computed(() => Math.max(1, ...r.value.rcaBreakdown.map(c => c.count)));

// ---- deep dive ----
const maxWh = computed(() => Math.max(1, ...dd.value.dispatchFindings.map(w => w.count)));
const splits = computed(() => [
  { key: "dtdc", name: "DTDC", s: dd.value.dtdcSplit, extra: x => (x.outForDeliveryCount != null ? `${x.outForDeliveryCount}× out for delivery` : "") },
  { key: "sfx", name: "Shadowfax", s: dd.value.shadowfaxSplit, extra: x => [x.account, x.reasonText && `“${x.reasonText}”`].filter(Boolean).join(" · ") },
  { key: "bd", name: "BlueDart", s: dd.value.bluedartSplit, extra: x => [x.outForDeliveryCount != null && `${x.outForDeliveryCount}× OFD`, x.reasonText && `“${x.reasonText}”`].filter(Boolean).join(" · ") },
].filter(x => x.s));
const noDataLines = computed(() => {
  const n = dd.value.noData;
  if (!n) return [];
  const lines = [`${n.total} late orders fall in this bucket; ${n.raftaarCount}/${n.total} (${n.raftaarPct.toFixed(0)}%) are DTDC Raftaar orders${n.nonRaftaarCount ? `, ${n.nonRaftaarCount} (${n.nonRaftaarPct.toFixed(0)}%) are not` : ""}.`];
  if (Object.keys(n.delayBuckets || {}).length) lines.push(`DTDC-Raftaar 2 PM cutoff rule applied to ${n.raftaarCount} Raftaar orders: ${Object.entries(n.delayBuckets).map(([k, v]) => `${v} ${k}`).join(", ")}.`);
  if (n.cityClusterFinding) lines.push(n.cityClusterFinding.text);
  if (n.warehouseWithinCityFinding) lines.push(n.warehouseWithinCityFinding.text);
  if (n.patternNote) lines.push(n.patternNote);
  if (n.nonRaftaarNote) lines.push(n.nonRaftaarNote);
  return lines;
});
const transitItems = computed(() => {
  const t = dd.value.transit;
  if (!t) return [];
  return [...t.lspCityFindings.map(f => f.text), ...(t.cityDateFinding ? [t.cityDateFinding.text] : []), ...(t.fallback?.text ? [t.fallback.text] : [])];
});
const maxLane = computed(() => Math.max(1, ...(dd.value.transit?.lspCityFindings || []).map(f => f.ratio)));

// ---- detail table ----
const tier = ref("all");
const catFilter = ref("all");
const detail = computed(() => r.value.detailRows.filter(x =>
  (tier.value === "all" || x.tier === tier.value) && (catFilter.value === "all" || x.rcaCategory === catFilter.value)));
function exportCsv() {
  downloadCsv(`late_deliveries_week_${props.p.week}.csv`,
    ["Source Order ID", "City", "Pincode", "Warehouse", "SKU Name", "LSP", "Docket No", "Promised TAT", "Actual TAT", "Dispatch Delay", "Post Dispatch Delay", "RCA"],
    detail.value.map(x => [x.sourceOrderId, x.city, x.pincode, x.warehouse, x.skuName, x.lsp, x.docketNo, x.promisedTat, x.actualTat, x.dispatchDelay, x.postDispatchDelay, x.rcaLabel]));
}
</script>

<template>
  <div class="rca-kpis">
    <div class="rca-kpi" style="--k: var(--sev-both)"><div class="label">Total late</div><div class="value">{{ r.totalLate }}</div><div class="sub">delivered after promise</div></div>
    <div class="rca-kpi" style="--k: var(--sla-s1)"><div class="label">Metro</div><div class="value">{{ r.metroLate }}</div><div class="sub">5 warehouse cities</div></div>
    <div class="rca-kpi" style="--k: var(--sla-s3)"><div class="label">Other</div><div class="value">{{ r.otherLate }}</div><div class="sub">all other cities</div></div>
  </div>

  <section class="sla-card">
    <div class="sla-card-head"><div><h3>Why orders were late</h3><p class="desc">Each late order is classified from its dispatch and post-dispatch delay. The CX Dependency split comes from the LSP tracking scans.</p></div></div>
    <div class="rca-mix">
      <div class="donut-wrap">
        <svg viewBox="0 0 190 190" width="190" height="190" role="img" aria-label="RCA mix">
          <circle cx="95" cy="95" :r="R" fill="none" stroke="var(--paper)" stroke-width="22" />
          <circle v-for="a in arcs" :key="a.category" cx="95" cy="95" :r="R" fill="none" :stroke="`var(--sev-${a.sev})`" stroke-width="22"
                  :stroke-dasharray="a.dash" :stroke-dashoffset="a.offset" transform="rotate(-90 95 95)"><title>{{ a.label }}: {{ a.count }}</title></circle>
        </svg>
        <div class="donut-center"><span class="n">{{ r.totalLate }}</span><span class="l">late orders</span></div>
      </div>
      <div class="bars">
        <div v-for="c in r.rcaBreakdown" :key="c.category" class="barrow">
          <div class="barrow-top"><span><span class="sev-pill" :class="`sev-${SEV[c.category]}`">{{ c.label }}</span></span><span class="v">{{ c.count }} · {{ c.pct.toFixed(0) }}%</span></div>
          <div class="barrow-track"><div class="barrow-fill" :class="`fill-${SEV[c.category]}`" :style="{ width: Math.max(2, (c.count / maxCat) * 100) + '%' }"></div></div>
        </div>
      </div>
    </div>
  </section>

  <section class="sla-card">
    <div class="sla-card-head"><div><div class="sla-card-step">Deep dive</div><h3>Root causes</h3><p class="desc">Inventory cross-checks use a live snapshot of current stock, not one taken for the analysed week, so treat any "stock-out contributed" flag as supporting evidence only.</p></div></div>
    <p v-if="r.uniwareNote" class="mini-note" style="margin:-4px 0 12px;">{{ r.uniwareNote }}</p>

    <div class="sla-grid-2">
      <div>
        <h4 class="section-title"><span class="sev-pill sev-dispatch">Warehouse dispatch delay</span></h4>
        <div v-if="!dd.dispatchFindings.length" class="mini-note">No dispatch-delay orders this week.</div>
        <div v-for="w in dd.dispatchFindings" :key="w.warehouse" class="insight">
          <div class="insight-head"><span class="mono">{{ w.warehouse }}</span><span class="v">{{ w.count }} orders</span></div>
          <div class="barrow-track" style="margin-bottom:6px;"><div class="barrow-fill fill-dispatch" :style="{ width: (w.count / maxWh) * 100 + '%' }"></div></div>
          <ul class="finding-list"><li v-for="(f, i) in w.findings" :key="i">{{ f.text }}</li></ul>
        </div>
      </div>

      <div>
        <h4 class="section-title"><span class="sev-pill sev-transit">Logistics / transit delay</span> <span v-if="dd.transit" class="mono" style="color:var(--muted); font-size:.75rem;">{{ dd.transit.totalTransitDelayed }} orders</span></h4>
        <div v-if="!dd.transit" class="mini-note">No transit-delay orders this week.</div>
        <template v-else>
          <div v-for="f in dd.transit.lspCityFindings" :key="f.lsp + f.city" class="barrow" style="margin-bottom:10px;">
            <div class="barrow-top"><span><span class="mono">{{ f.lsp }}</span> <span style="color:var(--muted)">· {{ f.city }}</span></span><span class="v">{{ f.count }}/{{ f.denom }} · {{ f.ratio.toFixed(1) }}×</span></div>
            <div class="barrow-track"><div class="barrow-fill fill-transit" :style="{ width: (f.ratio / maxLane) * 100 + '%' }"></div></div>
          </div>
          <ul v-if="transitItems.length" class="finding-list"><li v-for="(t, i) in transitItems" :key="i">{{ t }}</li></ul>
        </template>

        <div v-for="sp in splits" :key="sp.key" class="insight">
          <div class="insight-head">
            <span>{{ sp.name }} docket classification</span>
            <span class="v">{{ sp.s.byBucket.lsp_constraint }} LSP · {{ sp.s.byBucket.cx_dependency }} CX · {{ sp.s.byBucket.unclassified }} unclassified</span>
          </div>
          <ul class="finding-list scroll-y" style="max-height:220px;">
            <li v-for="x in sp.s.rows" :key="x.docketNo">
              <span class="sev-pill" :class="`sev-${CLASS_SEV[x.bucket]}`">{{ CLASS_LABEL[x.bucket] }}</span>
              <span class="mono" style="margin-left:6px;">{{ x.docketNo }}</span>
              <span v-if="sp.extra(x)" style="color:var(--muted)"> · {{ sp.extra(x) }}</span>
              <span> · {{ x.evidence }}</span>
            </li>
          </ul>
        </div>
      </div>
    </div>

    <h4 class="section-title" style="margin-top:14px;"><span class="sev-pill sev-nodata">No dispatch/transit tracking data</span></h4>
    <div v-if="!noDataLines.length" class="mini-note">No such orders this week.</div>
    <ul v-else class="finding-list"><li v-for="(t, i) in noDataLines" :key="i">{{ t }}</li></ul>
  </section>

  <section class="table-card" style="margin-bottom:16px;">
    <div class="sla-card-head" style="padding:14px 16px 0;">
      <div><h3>Late orders · detail</h3><p class="desc">Ordered by city (most late first), then by delivery date.</p></div>
      <div class="tbl-tools">
        <select v-model="tier"><option value="all">All cities</option><option value="Metro">Metro</option><option value="Other">Other</option></select>
        <select v-model="catFilter"><option value="all">All causes</option><option v-for="c in r.rcaBreakdown" :key="c.category" :value="c.category">{{ c.label }}</option></select>
        <span class="count">{{ detail.length }} orders</span>
        <button class="btn" @click="exportCsv">Download CSV</button>
      </div>
    </div>
    <div class="table-scroll scroll-y" style="margin-top:12px;">
      <table class="nowrap-table">
        <thead><tr>
          <th>Source Order ID</th><th>City</th><th>Pincode</th><th>Warehouse</th><th>SKU</th><th>LSP</th><th>Docket</th>
          <th class="num">Promised TAT</th><th class="num">Actual TAT</th><th class="num">Dispatch delay</th><th class="num">Post-dispatch</th><th>RCA</th>
        </tr></thead>
        <tbody>
          <tr v-for="x in detail" :key="x.sourceOrderId + x.docketNo">
            <td class="mono stripe" :class="`s-${SEV[x.rcaCategory]}`">{{ x.sourceOrderId }}</td>
            <td>{{ x.city }}</td><td class="mono">{{ dash(x.pincode) }}</td><td class="fac-code">{{ dash(x.warehouse) }}</td>
            <td>{{ dash(x.skuName) }}</td><td class="mono">{{ dash(x.lsp) }}</td><td class="mono">{{ dash(x.docketNo) }}</td>
            <td class="num mono">{{ dash(x.promisedTat) }}</td><td class="num mono">{{ dash(x.actualTat) }}</td>
            <td class="num mono">{{ dash(x.dispatchDelay) }}{{ x.uniwareDerived ? "†" : "" }}</td><td class="num mono">{{ dash(x.postDispatchDelay) }}</td>
            <td><span class="sev-pill" :class="`sev-${SEV[x.rcaCategory]}`">{{ x.rcaLabel }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="mini-note" style="padding:0 16px 14px;">
      † dispatch delay derived from Uniware's Sale Order Report (Created + Dispatch Date, 2 PM cutoff rule).
      <template v-if="r.missingPincodeCount"> Pincode is blank for {{ r.missingPincodeCount }} orders that weren't in the Uniware export or whose address is PII-masked.</template>
    </p>
  </section>
</template>
