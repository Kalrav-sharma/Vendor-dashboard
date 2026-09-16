<script setup>
// S&OP > Inventory Overview -- channel x SKU matrix + a channel-level
// DRR/DOI health view (replacing the old UC APP warehouse tables, which
// moved to their own "UC App + PLS" section -- see SopUcAppTab.vue).
//
// Redesigned 2026-09-15 per Anish: (a) Croma + Vijay Sales clubbed into one
// "MT" bucket, (b) the 3 UC-warehouse on-hand/in-transit/combined tables
// removed from this tab, (c) replaced with a channel x SKU DRR/DOI table
// (DRR = trailing 10-day actual sales average, DOI = forward walk against
// each channel's own "Expected Sale" daily-trackr series -- see
// scripts/sync_sop_inventory.py's compute_channel_drr_doi docstring).
import { computed } from "vue";
import { useSopInventoryData } from "../../composables/useSopInventoryData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const CHANNELS = [
  "UC App+PLS", "Amazon", "Flipkart", "DTDC Bangalore", "DTDC Gurgaon",
  "DTDC Kolkata", "SFX Mumbai", "SFX Hyderabad", "MT",
];
const DRR_DOI_CHANNELS = ["UC App+PLS", "Amazon", "Flipkart", "MT"];

const { channelRows, channelDrrDoiRows, loadError } = useSopInventoryData();

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

const drrDoiTable = computed(() => {
  const byKey = Object.fromEntries(channelDrrDoiRows.value.map(r => [`${r.channel}|${r.sku}`, r]));
  return DRR_DOI_CHANNELS.map(ch => ({
    channel: ch,
    cells: SKUS.map(s => byKey[`${ch}|${s}`]),
  }));
});

function doiClass(row) {
  if (!row) return "";
  if (row.doi_flag) return "cell-good"; // ">60" -- always well-stocked
  if (row.doi < 10) return "cell-critical";
  if (row.doi >= 30) return "cell-good";
  return "";
}
function doiText(row) {
  if (!row) return "–";
  if (row.doi_flag) return row.doi_flag; // ">60"
  return (Math.round(row.doi * 10) / 10).toLocaleString("en-IN");
}

const kpiTiles = computed(() => [
  { label: "Total network units", value: fmt(channelMatrix.value.totalRow.total) },
]);
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>

  <h3 class="section-title">Channel x SKU inventory</h3>
  <div class="table-card" style="margin-bottom: 28px;"><div class="table-scroll">
    <table>
      <thead><tr><th>Channel</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
      <tbody>
        <tr v-for="r in channelMatrix.body" :key="r.label">
          <td>{{ r.label }}</td>
          <td v-for="(c, i) in r.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
          <td class="num mono"><b>{{ fmt(r.total) }}</b></td>
        </tr>
        <tr class="row-total">
          <td>{{ channelMatrix.totalRow.label }}</td>
          <td v-for="(c, i) in channelMatrix.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
          <td class="num mono">{{ fmt(channelMatrix.totalRow.total) }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>

  <h3 class="section-title" style="margin-bottom: 6px;">Channel DRR / DOI</h3>
  <p class="field-hint" style="margin: 0 0 10px;">
    DRR: trailing 10-day average actual sales. DOI: forward-looking days of inventory against each
    channel's own daily sales plan.
    <span class="chip chip-critical" style="margin-left: 6px;">DOI &lt; 10</span>
    <span class="chip chip-good">DOI &#8805; 30</span>
  </p>
  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th rowspan="2">Channel</th>
          <th v-for="s in SKUS" :key="s" colspan="2" class="num">{{ s }}</th>
        </tr>
        <tr>
          <template v-for="s in SKUS" :key="s">
            <th class="num">DRR</th>
            <th class="num">DOI</th>
          </template>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in drrDoiTable" :key="r.channel">
          <td>{{ r.channel }}</td>
          <template v-for="(c, i) in r.cells" :key="i">
            <td class="num mono">{{ c ? fmt(c.drr) : "–" }}</td>
            <td class="num mono" :class="doiClass(c)">{{ doiText(c) }}</td>
          </template>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
