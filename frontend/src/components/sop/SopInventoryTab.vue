<script setup>
// S&OP > Inventory Overview -- channel x SKU matrix + UC APP warehouse view
// (on-hand / in-transit / combined), replicating /sop-master's Inventory
// Overview tab. No structural changes from that tab by design.
import { computed } from "vue";
import { useSopInventoryData } from "../../composables/useSopInventoryData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const CHANNELS = [
  "UC App+PLS", "Amazon", "Flipkart", "DTDC Bangalore", "DTDC Gurgaon",
  "DTDC Kolkata", "SFX Mumbai", "SFX Hyderabad", "Croma", "Vijay Sales",
];
const WAREHOUSES = ["Bangalore", "Gurgaon", "Hyderabad", "Mumbai", "Kolkata"];

const { channelRows, warehouseRows, loadError } = useSopInventoryData();

function fmt(n) {
  return Math.round(n || 0).toLocaleString("en-IN");
}

// Builds a { rowLabels x SKUS } matrix (plus a Total row and Total column)
// from a flat list of { <rowKey>: label, sku, [valueKey]: qty } rows.
function buildMatrix(rows, rowKey, rowLabels, valueKey) {
  const byRow = {};
  for (const label of rowLabels) byRow[label] = Object.fromEntries(SKUS.map(s => [s, 0]));
  for (const r of rows) {
    const label = r[rowKey];
    if (byRow[label] && SKUS.includes(r.sku)) byRow[label][r.sku] = r[valueKey] || 0;
  }
  const body = rowLabels.map(label => ({
    label,
    cells: SKUS.map(s => byRow[label][s]),
    total: SKUS.reduce((sum, s) => sum + byRow[label][s], 0),
  }));
  const totalRow = {
    label: "Total",
    cells: SKUS.map((_, i) => body.reduce((sum, r) => sum + r.cells[i], 0)),
    total: body.reduce((sum, r) => sum + r.total, 0),
  };
  return { body, totalRow };
}

const channelMatrix = computed(() => buildMatrix(channelRows.value, "channel", CHANNELS, "qty"));
const onHandMatrix = computed(() => buildMatrix(warehouseRows.value, "warehouse", WAREHOUSES, "on_hand"));
const inTransitMatrix = computed(() => buildMatrix(warehouseRows.value, "warehouse", WAREHOUSES, "in_transit"));
const combinedMatrix = computed(() => buildMatrix(warehouseRows.value, "warehouse", WAREHOUSES, "combined"));

const kpiTiles = computed(() => [
  { label: "Total network units", value: fmt(channelMatrix.value.totalRow.total) },
  { label: "UC on-hand", value: fmt(onHandMatrix.value.totalRow.total) },
  { label: "UC in-transit", value: fmt(inTransitMatrix.value.totalRow.total) },
  { label: "UC combined", value: fmt(combinedMatrix.value.totalRow.total) },
]);
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>

  <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Channel x SKU inventory</h3>
  <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
    <table>
      <thead><tr><th>Channel</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
      <tbody>
        <tr v-for="r in channelMatrix.body" :key="r.label">
          <td>{{ r.label }}</td>
          <td v-for="(c, i) in r.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
          <td class="num mono"><b>{{ fmt(r.total) }}</b></td>
        </tr>
        <tr style="font-weight: 600;">
          <td>{{ channelMatrix.totalRow.label }}</td>
          <td v-for="(c, i) in channelMatrix.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
          <td class="num mono">{{ fmt(channelMatrix.totalRow.total) }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>

  <h3 style="font-size: 0.95rem; margin: 0 0 10px;">UC APP warehouse view -- on-hand</h3>
  <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
    <table>
      <thead><tr><th>Warehouse</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
      <tbody>
        <tr v-for="r in onHandMatrix.body" :key="r.label">
          <td>{{ r.label }}</td>
          <td v-for="(c, i) in r.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
          <td class="num mono"><b>{{ fmt(r.total) }}</b></td>
        </tr>
        <tr style="font-weight: 600;">
          <td>{{ onHandMatrix.totalRow.label }}</td>
          <td v-for="(c, i) in onHandMatrix.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
          <td class="num mono">{{ fmt(onHandMatrix.totalRow.total) }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>

  <h3 style="font-size: 0.95rem; margin: 0 0 10px;">UC APP warehouse view -- in-transit</h3>
  <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
    <table>
      <thead><tr><th>Warehouse</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
      <tbody>
        <tr v-for="r in inTransitMatrix.body" :key="r.label">
          <td>{{ r.label }}</td>
          <td v-for="(c, i) in r.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
          <td class="num mono"><b>{{ fmt(r.total) }}</b></td>
        </tr>
        <tr style="font-weight: 600;">
          <td>{{ inTransitMatrix.totalRow.label }}</td>
          <td v-for="(c, i) in inTransitMatrix.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
          <td class="num mono">{{ fmt(inTransitMatrix.totalRow.total) }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>

  <h3 style="font-size: 0.95rem; margin: 0 0 10px;">UC APP warehouse view -- on-hand + in-transit</h3>
  <div class="table-card"><div class="table-scroll">
    <table>
      <thead><tr><th>Warehouse</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
      <tbody>
        <tr v-for="r in combinedMatrix.body" :key="r.label">
          <td>{{ r.label }}</td>
          <td v-for="(c, i) in r.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
          <td class="num mono"><b>{{ fmt(r.total) }}</b></td>
        </tr>
        <tr style="font-weight: 600;">
          <td>{{ combinedMatrix.totalRow.label }}</td>
          <td v-for="(c, i) in combinedMatrix.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
          <td class="num mono">{{ fmt(combinedMatrix.totalRow.total) }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
