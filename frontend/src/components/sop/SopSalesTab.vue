<script setup>
// S&OP > Sales: Plan vs Actual -- current month, channel x SKU: Projection
// (derived from the Dashboard tab's Sale plan x Channel Split), Actual
// (from the "actual sales" tab), and Gap (Actual - Projection). No
// structural changes from /sop-master's original tab.
import { computed } from "vue";
import { useSopSalesData } from "../../composables/useSopSalesData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const CHANNELS = ["UC App+PLS", "Amazon", "Flipkart", "MT"];

const { rows, loadError } = useSopSalesData();

function fmt(n) {
  return Math.round(n || 0).toLocaleString("en-IN");
}

function buildMatrix(valueKey) {
  const byChannel = {};
  for (const ch of CHANNELS) byChannel[ch] = Object.fromEntries(SKUS.map(s => [s, 0]));
  for (const r of rows.value) {
    if (byChannel[r.channel] && SKUS.includes(r.sku)) byChannel[r.channel][r.sku] = r[valueKey] || 0;
  }
  const body = CHANNELS.map(ch => ({
    label: ch,
    cells: SKUS.map(s => byChannel[ch][s]),
    total: SKUS.reduce((sum, s) => sum + byChannel[ch][s], 0),
  }));
  const totalRow = {
    label: "Total",
    cells: SKUS.map((_, i) => body.reduce((sum, r) => sum + r.cells[i], 0)),
    total: body.reduce((sum, r) => sum + r.total, 0),
  };
  return { body, totalRow };
}

const projectionMatrix = computed(() => buildMatrix("projection"));
const actualMatrix = computed(() => buildMatrix("actual"));
const gapMatrix = computed(() => buildMatrix("gap"));

const othersActual = computed(() => {
  const total = rows.value.filter(r => r.channel === "Others").reduce((sum, r) => sum + (r.actual || 0), 0);
  return total;
});

const monthLabel = computed(() => {
  const ms = rows.value[0]?.month_start;
  if (!ms) return "";
  return new Date(ms + "T00:00:00").toLocaleDateString("en-IN", { month: "long", year: "numeric" });
});

const kpiTiles = computed(() => [
  { label: "Projection (total)", value: fmt(projectionMatrix.value.totalRow.total) },
  { label: "Actual (total)", value: fmt(actualMatrix.value.totalRow.total) },
  { label: "Gap", value: fmt(gapMatrix.value.totalRow.total), cls: gapMatrix.value.totalRow.total < 0 ? "critical" : "good" },
]);

</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>
  <p v-if="monthLabel" class="scope" style="margin: -8px 0 16px;">Current month: {{ monthLabel }}</p>

  <div v-if="othersActual" class="chip chip-muted" style="display: inline-block; margin-bottom: 16px;">
    Note: "Others" channel has {{ fmt(othersActual) }} actual units with no plan/projection counterpart in the Dashboard tab.
  </div>

  <template v-for="section in [
    { title: 'Projection', matrix: projectionMatrix },
    { title: 'Actual', matrix: actualMatrix },
    { title: 'Gap (Actual - Projection)', matrix: gapMatrix },
  ]" :key="section.title">
    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">{{ section.title }}</h3>
    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead><tr><th>Channel</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
        <tbody>
          <tr v-for="r in section.matrix.body" :key="r.label">
            <td>{{ r.label }}</td>
            <td v-for="(c, i) in r.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
            <td class="num mono"><b>{{ fmt(r.total) }}</b></td>
          </tr>
          <tr style="font-weight: 600;">
            <td>{{ section.matrix.totalRow.label }}</td>
            <td v-for="(c, i) in section.matrix.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
            <td class="num mono">{{ fmt(section.matrix.totalRow.total) }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
