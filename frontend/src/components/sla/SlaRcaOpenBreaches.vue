<script setup>
// RCA › Open SLA Breaches: orders promised in the week window around the RCA week
// (N−1 to N+1) that are still undelivered past their promised date. It's the live
// backlog as of the sync.
import { ref, computed } from "vue";
import { CITY_FILTERS, matchGroup, BUCKET_SEV, dash, downloadCsv } from "./slaUtil.js";

const props = defineProps({ p: { type: Object, required: true } });
const o = computed(() => props.p.openSla);

const f = ref("all");
const bucket = ref("all");
const rows = computed(() => (o.value ? o.value.rows.filter(r => matchGroup(r.cityGroup, f.value) && (bucket.value === "all" || r.breachBucket === bucket.value)) : []));
const maxB = computed(() => Math.max(1, ...(o.value?.summary.bucketCounts || []).map(b => b.count)));
const topCities = computed(() => (o.value?.summary.cityCounts || []).slice(0, 8));
const maxC = computed(() => Math.max(1, ...topCities.value.map(c => c.count)));
function exportCsv() {
  downloadCsv(`open_sla_breaches_week_${props.p.week}.csv`,
    ["Source Order ID", "City", "LSP", "Docket No", "Shipment Status", "Days Past SLA", "Order Created", "Promised Delivery", "Breach Bucket"],
    rows.value.map(r => [r.sourceOrderId, r.cityDisplay, r.lsp, r.docketNo, r.shipmentStatus, r.daysPastSla, r.rootCreatedAt, r.promisedDeliveryDate, r.breachBucketLabel]));
}
</script>

<template>
  <div v-if="!o" class="sla-card empty-state">Open-breach data unavailable this run.</div>
  <template v-else>
    <p class="mini-note" style="margin:0 0 12px;">Orders promised for delivery in {{ o.weekLabel }} that are still open and past their promised date. This is the live backlog as of the last sync.</p>
    <div class="rca-kpis">
      <div class="rca-kpi" style="--k: var(--sev-both)"><div class="label">Open breaches</div><div class="value">{{ o.summary.total }}</div><div class="sub">undelivered, past promise</div></div>
      <div class="rca-kpi" style="--k: var(--sev-dispatch)"><div class="label">Oldest breach</div><div class="value">{{ o.summary.oldestBreach }}d</div><div class="sub">days past SLA</div></div>
      <div class="rca-kpi" style="--k: var(--accent)"><div class="label">Worst-hit city</div><div class="value" style="font-size:1.25rem;">{{ o.summary.worstCity ? o.summary.worstCity.city : "–" }}</div><div class="sub">{{ o.summary.worstCity ? `${o.summary.worstCity.count} open` : "" }}</div></div>
    </div>

    <div class="sla-grid-2">
      <section class="sla-card">
        <div class="sla-card-head"><div><h3>By breach age</h3></div></div>
        <div class="bars">
          <div v-for="b in o.summary.bucketCounts" :key="b.bucket" class="barrow">
            <div class="barrow-top"><span><span class="sev-pill" :class="`sev-${BUCKET_SEV[b.bucket]}`">{{ b.label }}</span></span><span class="v">{{ b.count }}</span></div>
            <div class="barrow-track"><div class="barrow-fill" :class="`fill-${BUCKET_SEV[b.bucket]}`" :style="{ width: Math.max(2, (b.count / maxB) * 100) + '%' }"></div></div>
          </div>
        </div>
      </section>
      <section class="sla-card">
        <div class="sla-card-head"><div><h3>By city</h3><p class="desc">Top 8 cities.</p></div></div>
        <div class="bars">
          <div v-for="c in topCities" :key="c.city" class="barrow">
            <div class="barrow-top"><span>{{ c.city }}</span><span class="v">{{ c.count }}</span></div>
            <div class="barrow-track"><div class="barrow-fill" :style="{ width: (c.count / maxC) * 100 + '%' }"></div></div>
          </div>
        </div>
      </section>
    </div>

    <section class="table-card" style="margin-bottom:16px;">
      <div class="sla-card-head" style="padding:14px 16px 0;">
        <div><h3>Open breaches · detail</h3><p class="desc">Worst breach first.</p></div>
        <div class="tbl-tools">
          <select v-model="f"><option v-for="x in CITY_FILTERS" :key="x.id" :value="x.id">{{ x.label }}</option></select>
          <select v-model="bucket"><option value="all">All buckets</option><option v-for="b in o.summary.bucketCounts" :key="b.bucket" :value="b.bucket">{{ b.label }}</option></select>
          <span class="count">{{ rows.length }} orders</span>
          <button class="btn" @click="exportCsv">Download CSV</button>
        </div>
      </div>
      <div class="table-scroll scroll-y" style="margin-top:12px;">
        <table class="nowrap-table">
          <thead><tr><th>Source Order ID</th><th>City</th><th>LSP</th><th>Docket</th><th>Shipment status</th><th class="num">Days past SLA</th><th>Created</th><th>Promised</th><th>Bucket</th></tr></thead>
          <tbody>
            <tr v-for="r in rows" :key="r.sourceOrderId + r.docketNo">
              <td class="mono stripe" :class="`s-${BUCKET_SEV[r.breachBucket]}`">{{ r.sourceOrderId }}</td>
              <td>{{ r.cityDisplay }}</td><td class="mono">{{ dash(r.lsp) }}</td><td class="mono">{{ dash(r.docketNo) }}</td>
              <td>{{ dash(r.shipmentStatus) }}</td><td class="num mono">{{ dash(r.daysPastSla) }}</td>
              <td class="mono">{{ dash(r.rootCreatedAt) }}</td><td class="mono">{{ dash(r.promisedDeliveryDate) }}</td>
              <td><span class="sev-pill" :class="`sev-${BUCKET_SEV[r.breachBucket]}`">{{ r.breachBucketLabel }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
