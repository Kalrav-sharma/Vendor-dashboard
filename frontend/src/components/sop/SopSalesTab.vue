<script setup>
// S&OP > Sales: Plan vs Actual -- current month, channel x SKU.
//
// The two sides deliberately cover DIFFERENT periods (Anish's explicit
// choice when asked): Projection is the FULL month's plan, Actual is only
// month-to-date, so Gap reads "how much of the month's plan is still left
// to sell". Every heading says which period it is, because comparing a
// 30-day plan against 16 days of sales is otherwise easy to misread.
// Actual comes from the same daily series as the Day-on-Day Sales tab (see
// build_actuals_for_month in sync_sop_sales.py), so the two tabs always tie.
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
// The DB's `gap` column is actual - projection, which mid-month is hugely negative for everything
// and reads like failure. Since projection here is the FULL month and actual is only month-to-date,
// the honest framing is the other way round: projection - actual = units still to sell. A negative
// value then genuinely means the channel has already beaten its whole-month plan.
const remainingMatrix = computed(() => {
  const proj = projectionMatrix.value, act = actualMatrix.value;
  return {
    body: proj.body.map((r, i) => ({
      label: r.label,
      cells: r.cells.map((c, j) => c - act.body[i].cells[j]),
      total: r.total - act.body[i].total,
    })),
    totalRow: {
      label: "Total",
      cells: proj.totalRow.cells.map((c, j) => c - act.totalRow.cells[j]),
      total: proj.totalRow.total - act.totalRow.total,
    },
  };
});

const othersActual = computed(() => {
  const total = rows.value.filter(r => r.channel === "Others").reduce((sum, r) => sum + (r.actual || 0), 0);
  return total;
});

const monthLabel = computed(() => {
  const ms = rows.value[0]?.month_start;
  if (!ms) return "";
  return new Date(ms + "T00:00:00").toLocaleDateString("en-IN", { month: "long", year: "numeric" });
});
// "1-16 Sept" -- the window the Actual side actually covers.
const mtdLabel = computed(() => {
  const now = new Date();
  return `1–${now.getDate()} ${now.toLocaleDateString("en-IN", { month: "short" })}`;
});

const kpiTiles = computed(() => [
  { label: "Projection (full month)", value: fmt(projectionMatrix.value.totalRow.total) },
  { label: "Actual (month to date)", value: fmt(actualMatrix.value.totalRow.total) },
  { label: "Still to sell", value: fmt(remainingMatrix.value.totalRow.total) },
]);

</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>
  <p v-if="monthLabel" class="scope" style="margin: -8px 0 16px;">
    <b>{{ monthLabel }}</b> &middot; projection is the <b>full month</b>, actual is <b>{{ mtdLabel }}</b> so far
  </p>

  <div v-if="othersActual" class="chip chip-muted" style="display: inline-block; margin-bottom: 16px;">
    Note: "Others" channel has {{ fmt(othersActual) }} actual units (channel total only, no SKU split) with no plan counterpart.
  </div>

  <template v-for="section in [
    { title: `Projection — full ${monthLabel}`, matrix: projectionMatrix, signed: false },
    { title: `Actual — month to date (${mtdLabel})`, matrix: actualMatrix, signed: false },
    { title: 'Still to sell (Projection − Actual)', matrix: remainingMatrix, signed: true },
  ]" :key="section.title">
    <h3 class="section-title">{{ section.title }}</h3>
    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead><tr><th>Channel</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
        <tbody>
          <tr v-for="r in section.matrix.body" :key="r.label">
            <td>{{ r.label }}</td>
            <td v-for="(c, i) in r.cells" :key="i" class="num mono"
                :class="section.signed && c <= 0 ? 'cell-good' : ''">{{ fmt(c) }}</td>
            <td class="num mono"><b>{{ fmt(r.total) }}</b></td>
          </tr>
          <tr class="row-total">
            <td>{{ section.matrix.totalRow.label }}</td>
            <td v-for="(c, i) in section.matrix.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
            <td class="num mono">{{ fmt(section.matrix.totalRow.total) }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
