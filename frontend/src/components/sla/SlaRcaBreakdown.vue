<script setup>
// RCA › RCA Breakdown: the cause mix, then one collapsible panel per root cause
// (dispatch, transit + LSP docket verdicts, no tracking data), then the late-order
// detail table. Every figure comes from the payload. Nothing is recomputed here.
// Laid out as summary-first: each panel's closed row carries a count and a headline,
// and the long per-docket evidence is folded behind a "Show dockets" disclosure.
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
const countOf = cat => r.value.rcaBreakdown.find(c => c.category === cat)?.count || 0;

// ---- root-cause panels ----
const dispatch = computed(() => dd.value.dispatchFindings || []);
const transit = computed(() => dd.value.transit);
const transitItems = computed(() => {
  const t = transit.value;
  if (!t) return [];
  return [...t.lspCityFindings.map(f => f.text), ...(t.cityDateFinding ? [t.cityDateFinding.text] : []), ...(t.fallback?.text ? [t.fallback.text] : [])];
});
const maxLane = computed(() => Math.max(1, ...(transit.value?.lspCityFindings || []).map(f => f.ratio)));
const noDataLines = computed(() => {
  const n = dd.value.noData;
  if (!n) return [];
  const lines = [`${n.raftaarCount}/${n.total} (${n.raftaarPct.toFixed(0)}%) are DTDC Raftaar orders${n.nonRaftaarCount ? `, ${n.nonRaftaarCount} (${n.nonRaftaarPct.toFixed(0)}%) are not` : ""}.`];
  if (Object.keys(n.delayBuckets || {}).length) lines.push(`2 PM cutoff rule applied to ${n.raftaarCount} Raftaar orders: ${Object.entries(n.delayBuckets).map(([k, v]) => `${v} ${k}`).join(", ")}.`);
  if (n.cityClusterFinding) lines.push(n.cityClusterFinding.text);
  if (n.warehouseWithinCityFinding) lines.push(n.warehouseWithinCityFinding.text);
  if (n.patternNote) lines.push(n.patternNote);
  if (n.nonRaftaarNote) lines.push(n.nonRaftaarNote);
  return lines;
});

// One-line headline for each closed panel
const headline = computed(() => ({
  dispatch: dispatch.value.length
    ? `${dispatch.value.length} warehouse${dispatch.value.length > 1 ? "s" : ""}, most in ${dispatch.value[0].warehouse}`
    : "No dispatch-delay orders this week",
  transit: transit.value
    ? (transit.value.lspCityFindings[0]
      ? `${transit.value.lspCityFindings[0].lsp} in ${transit.value.lspCityFindings[0].city} is ${transit.value.lspCityFindings[0].ratio.toFixed(1)}× the week's transit-delay rate`
      : "No LSP–city concentration beyond noise")
    : "No transit-delay orders this week",
  nodata: dd.value.noData ? `${dd.value.noData.raftaarCount} Raftaar · ${dd.value.noData.nonRaftaarCount} other LSPs` : "No such orders this week",
}));

// LSP docket verdicts: one summary bar per LSP, one combined docket table
const verdicts = computed(() => [
  { name: "DTDC", s: dd.value.dtdcSplit },
  { name: "Shadowfax", s: dd.value.shadowfaxSplit },
  { name: "BlueDart", s: dd.value.bluedartSplit },
].filter(v => v.s).map(v => ({ name: v.name, total: v.s.total, ...v.s.byBucket })));
const dockets = computed(() => [
  ...(dd.value.dtdcSplit?.rows || []).map(x => ({ lsp: "DTDC", detail: x.outForDeliveryCount != null ? `${x.outForDeliveryCount}× out for delivery` : "", ...x })),
  ...(dd.value.shadowfaxSplit?.rows || []).map(x => ({ lsp: "Shadowfax", detail: [x.account, x.reasonText && `“${x.reasonText}”`].filter(Boolean).join(" · "), ...x })),
  ...(dd.value.bluedartSplit?.rows || []).map(x => ({ lsp: "BlueDart", detail: [x.outForDeliveryCount != null && `${x.outForDeliveryCount}× OFD`, x.reasonText && `“${x.reasonText}”`].filter(Boolean).join(" · "), ...x })),
]);
const seg = (n, t) => `${t ? (n / t) * 100 : 0}%`;

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
  <!-- Cause mix -->
  <section class="sla-card">
    <div class="sla-card-head"><div><h3>Why orders were late · week {{ p.week }}</h3><p class="desc">Classified from each order's dispatch and post-dispatch delay. CX Dependency comes from the LSP tracking scans.</p></div></div>
    <div class="rca-mix">
      <div style="display:flex; flex-direction:column; align-items:center; gap:8px;">
        <div class="donut-wrap">
          <svg viewBox="0 0 190 190" width="190" height="190" role="img" aria-label="RCA mix">
            <circle cx="95" cy="95" :r="R" fill="none" stroke="var(--paper)" stroke-width="22" />
            <circle v-for="a in arcs" :key="a.category" cx="95" cy="95" :r="R" fill="none" :stroke="`var(--sev-${a.sev})`" stroke-width="22"
                    :stroke-dasharray="a.dash" :stroke-dashoffset="a.offset" transform="rotate(-90 95 95)"><title>{{ a.label }}: {{ a.count }}</title></circle>
          </svg>
          <div class="donut-center"><span class="n">{{ r.totalLate }}</span><span class="l">late orders</span></div>
        </div>
        <span class="mini-note" style="margin:0;">{{ r.metroLate }} metro · {{ r.otherLate }} other cities</span>
      </div>
      <table class="legend-table">
        <thead><tr><th>Cause</th><th class="num">Orders</th><th class="num">Share</th></tr></thead>
        <tbody>
          <tr v-for="c in r.rcaBreakdown" :key="c.category">
            <td><span class="dot" :style="{ background: `var(--sev-${SEV[c.category]})` }"></span>{{ c.label }}</td>
            <td class="num mono">{{ c.count }}</td>
            <td class="num mono">
              <span class="share-bar"><span :class="`fill-${SEV[c.category]}`" :style="{ width: c.pct + '%' }"></span></span>{{ c.pct.toFixed(0) }}%
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <!-- Root causes -->
  <section class="sla-card">
    <div class="sla-card-head"><div><div class="sla-card-step">Deep dive</div><h3>Root causes</h3><p class="desc">Click a cause to expand it.</p></div></div>

    <details class="rc-panel">
      <summary>
        <span class="sev-pill sev-dispatch">Warehouse dispatch delay</span>
        <span class="rc-count mono">{{ countOf('dispatch') }}</span>
        <span class="rc-headline">{{ headline.dispatch }}</span>
        <span class="rc-chev" aria-hidden="true">›</span>
      </summary>
      <div class="rc-body">
        <table v-if="dispatch.length" class="rc-table">
          <thead><tr><th>Warehouse</th><th class="num">Orders</th><th>Finding</th></tr></thead>
          <tbody>
            <tr v-for="w in dispatch" :key="w.warehouse">
              <td class="mono">{{ w.warehouse }}</td><td class="num mono">{{ w.count }}</td>
              <td>{{ w.findings.map(f => f.text).join(" ") }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="mini-note">No dispatch-delay orders this week.</p>
      </div>
    </details>

    <details class="rc-panel" open>
      <summary>
        <span class="sev-pill sev-transit">Logistics / transit delay</span>
        <span class="rc-count mono">{{ transit ? transit.totalTransitDelayed : 0 }}</span>
        <span class="rc-headline">{{ headline.transit }}</span>
        <span class="rc-chev" aria-hidden="true">›</span>
      </summary>
      <div class="rc-body">
        <p v-if="!transit" class="mini-note">No transit-delay orders this week.</p>
        <template v-else>
          <p class="mini-note" style="margin:0 0 10px;">All {{ transit.totalTransitDelayed }} orders with a post-dispatch delay: {{ countOf('transit') }} transit, {{ countOf('cx_dependency') }} later found CX-dependent, and {{ countOf('both') }} combined dispatch + transit.</p>
          <div v-if="transit.lspCityFindings.length" class="bars" style="margin-bottom:10px;">
            <div v-for="f in transit.lspCityFindings" :key="f.lsp + f.city" class="barrow">
              <div class="barrow-top"><span><span class="mono">{{ f.lsp }}</span> <span style="color:var(--muted)">· {{ f.city }}</span></span><span class="v">{{ f.count }}/{{ f.denom }} late · {{ f.ratio.toFixed(1) }}× normal</span></div>
              <div class="barrow-track"><div class="barrow-fill fill-transit" :style="{ width: (f.ratio / maxLane) * 100 + '%' }"></div></div>
            </div>
          </div>
          <ul v-if="transitItems.length" class="finding-list">
            <li v-for="(t, i) in transitItems.slice(transit.lspCityFindings.length)" :key="i">{{ t }}</li>
          </ul>

          <div v-if="verdicts.length" class="rc-sub">
            <div class="rc-sub-title">Docket verdicts from LSP tracking</div>
            <div v-for="v in verdicts" :key="v.name" class="verdict-row">
              <span class="verdict-name">{{ v.name }}</span>
              <span class="verdict-bar" :title="`${v.lsp_constraint} LSP constraint · ${v.cx_dependency} CX dependency · ${v.unclassified} unclassified`">
                <span class="fill-transit" :style="{ width: seg(v.lsp_constraint, v.total) }"></span>
                <span class="fill-cx" :style="{ width: seg(v.cx_dependency, v.total) }"></span>
                <span class="fill-nodata" :style="{ width: seg(v.unclassified, v.total) }"></span>
              </span>
              <span class="verdict-counts mono">{{ v.lsp_constraint }} LSP · {{ v.cx_dependency }} CX<template v-if="v.unclassified"> · {{ v.unclassified }} ?</template></span>
            </div>
            <div class="verdict-legend">
              <span><span class="dot" style="background:var(--sev-transit)"></span>LSP constraint</span>
              <span><span class="dot" style="background:var(--sev-cx)"></span>CX dependency</span>
              <span><span class="dot" style="background:var(--sev-nodata)"></span>Unclassified</span>
            </div>
            <details class="rc-inner">
              <summary>Show {{ dockets.length }} dockets</summary>
              <div class="table-scroll scroll-y" style="max-height:320px;">
                <table class="rc-table">
                  <thead><tr><th>LSP</th><th>Docket</th><th>Verdict</th><th>Reason / evidence</th></tr></thead>
                  <tbody>
                    <tr v-for="x in dockets" :key="x.lsp + x.docketNo">
                      <td>{{ x.lsp }}</td><td class="mono">{{ x.docketNo }}</td>
                      <td><span class="sev-pill" :class="`sev-${CLASS_SEV[x.bucket]}`">{{ CLASS_LABEL[x.bucket] }}</span></td>
                      <td><span v-if="x.detail" style="color:var(--muted)">{{ x.detail }} · </span>{{ x.evidence }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>
          </div>
        </template>
      </div>
    </details>

    <details class="rc-panel">
      <summary>
        <span class="sev-pill sev-nodata">No dispatch/transit tracking data</span>
        <span class="rc-count mono">{{ countOf('no_data') }}</span>
        <span class="rc-headline">{{ headline.nodata }}</span>
        <span class="rc-chev" aria-hidden="true">›</span>
      </summary>
      <div class="rc-body">
        <ul v-if="noDataLines.length" class="finding-list"><li v-for="(t, i) in noDataLines" :key="i">{{ t }}</li></ul>
        <p v-else class="mini-note">No such orders this week.</p>
      </div>
    </details>

    <p class="mini-note" style="margin-top:12px;">
      Stock-out flags use today's inventory snapshot, not one taken for this week, so treat them as supporting evidence only.
      <template v-if="r.uniwareNote"> {{ r.uniwareNote }}</template>
    </p>
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
