<script setup>
// S&OP > UC App + PLS -- new section (2026-09-15, per Anish) sitting
// between Inventory Overview and Sales: Plan vs Actual. This phase covers
// the 5 UC warehouses only: on-hand, in-transit (with a toggle to add
// on-hand back in), and a DRR/DOI health view.
//
// Individual dark-store on-hand inventory (item 2c) and the dark-store rows
// of the DRR/DOI view are deferred to a separate Uniware-sync follow-up --
// the Google Sheet only has each DTDC/SFX city's aggregated total (already
// shown on Inventory Overview), not per-locality on-hand.
import { computed, ref } from "vue";
import { useSopUcAppData } from "../../composables/useSopUcAppData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const WAREHOUSES = ["Bangalore", "Gurgaon", "Hyderabad", "Mumbai", "Kolkata"];
const VIEWS = [
  { key: "on-hand", label: "Warehouse on-hand" },
  { key: "in-transit", label: "In-transit" },
  { key: "drr-doi", label: "DRR / DOI" },
];
const IN_TRANSIT_MODES = [
  { key: "in_transit", label: "In-Transit only" },
  { key: "combined", label: "On-Hand + In-Transit" },
];

const { warehouseRows, facilityDrrDoiRows, loadError } = useSopUcAppData();

const activeView = ref(VIEWS[0].key);
const inTransitMode = ref(IN_TRANSIT_MODES[0].key);

function fmt(n) {
  return Math.round(n || 0).toLocaleString("en-IN");
}

function buildMatrix(rows, valueKey) {
  const byRow = {};
  for (const wh of WAREHOUSES) byRow[wh] = Object.fromEntries(SKUS.map(s => [s, 0]));
  for (const r of rows) {
    if (byRow[r.warehouse] && SKUS.includes(r.sku)) byRow[r.warehouse][r.sku] = r[valueKey] || 0;
  }
  const body = WAREHOUSES.map(wh => ({
    label: wh,
    cells: SKUS.map(s => byRow[wh][s]),
    total: SKUS.reduce((sum, s) => sum + byRow[wh][s], 0),
  }));
  const totalRow = {
    label: "Total",
    cells: SKUS.map((_, i) => body.reduce((sum, r) => sum + r.cells[i], 0)),
    total: body.reduce((sum, r) => sum + r.total, 0),
  };
  return { body, totalRow };
}

const onHandMatrix = computed(() => buildMatrix(warehouseRows.value, "on_hand"));
const inTransitMatrix = computed(() => buildMatrix(warehouseRows.value, inTransitMode.value));

const drrDoiTable = computed(() => {
  const byKey = Object.fromEntries(facilityDrrDoiRows.value.map(r => [`${r.facility}|${r.sku}`, r]));
  return WAREHOUSES.map(wh => ({
    warehouse: wh,
    cells: SKUS.map(s => byKey[`${wh}|${s}`]),
  }));
});

function doiClass(row) {
  if (!row || row.doi == null) return "";
  if (row.doi < 10) return "critical";
  if (row.doi >= 30) return "good";
  return "";
}
function doiText(row) {
  if (!row) return "–";
  return row.doi == null ? "–" : (Math.round(row.doi * 10) / 10).toLocaleString("en-IN");
}

const kpiTiles = computed(() => [
  { label: "UC on-hand", value: fmt(onHandMatrix.value.totalRow.total) },
  { label: "UC in-transit", value: fmt(buildMatrix(warehouseRows.value, "in_transit").totalRow.total) },
  { label: "UC combined", value: fmt(buildMatrix(warehouseRows.value, "combined").totalRow.total) },
]);
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>

  <div class="subtabs" style="margin-bottom: 18px;">
    <button
      v-for="v in VIEWS" :key="v.key"
      class="subtab-item" :class="{ active: activeView === v.key }"
      @click="activeView = v.key"
    >{{ v.label }}</button>
  </div>

  <div v-show="activeView === 'on-hand'">
    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Warehouse on-hand</h3>
    <div class="table-card"><div class="table-scroll">
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
  </div>

  <div v-show="activeView === 'in-transit'">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
      <h3 style="font-size: 0.95rem; margin: 0;">In-transit</h3>
      <div style="display: flex; gap: 14px;">
        <label v-for="m in IN_TRANSIT_MODES" :key="m.key" style="display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 0.85rem;">
          <input type="radio" :value="m.key" v-model="inTransitMode"> {{ m.label }}
        </label>
      </div>
    </div>
    <div class="table-card"><div class="table-scroll">
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
  </div>

  <div v-show="activeView === 'drr-doi'">
    <h3 style="font-size: 0.95rem; margin: 0 0 6px;">Warehouse DRR / DOI</h3>
    <p class="field-hint" style="margin: 0 0 10px;">
      DRR: trailing 10-day average direct warehouse sales. DOI: on-hand / DRR.
      <span class="chip chip-critical" style="margin-left: 6px;">DOI &lt; 10</span>
      <span class="chip chip-good">DOI &#8805; 30 (warehouses)</span>
    </p>
    <p class="field-hint" style="margin: 0 0 10px;">
      Dark-store rows (DOI &#8805; 10 = green there, a lower bar than warehouses) land once individual
      dark-store on-hand is synced from Uniware directly -- the sheet only has each city's
      aggregated total today.
    </p>
    <div class="table-card"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th rowspan="2">Warehouse</th>
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
          <tr v-for="r in drrDoiTable" :key="r.warehouse">
            <td>{{ r.warehouse }}</td>
            <template v-for="(c, i) in r.cells" :key="i">
              <td class="num mono">{{ c ? fmt(c.drr) : "–" }}</td>
              <td class="num mono" :class="doiClass(c)">{{ doiText(c) }}</td>
            </template>
          </tr>
        </tbody>
      </table>
    </div></div>
  </div>
</template>
