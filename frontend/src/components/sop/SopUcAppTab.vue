<script setup>
// S&OP > UC App + PLS -- new section (2026-09-15, per Anish) sitting
// between Inventory Overview and Sales: Plan vs Actual. Covers the 5 UC
// warehouses AND, since 2026-09-16, the individual dark stores within each
// DTDC/SFX bucket -- on-hand, in-transit (with a toggle to add on-hand back
// in), and a DRR/DOI health view.
//
// Each DTDC/SFX bucket in "Current Inventory" turned out to be an aggregate
// label over multiple individual dark stores (confirmed live 2026-09-16,
// correcting an earlier wrong assumption that only city-aggregated totals
// existed) -- 21 stores total, 19 of which also have their own "UC sales
// trackr" DRR block (2 don't, and so have no DRR/DOI row -- see
// DARK_STORE_TITLE_COLS in sync_sop_inventory.py).
//
// On-hand is live Uniware stock (sop_uniware_inventory, since 2026-09-16) --
// hence the "Stock as of" stamp on that view: it's the one figure here that
// nobody can sanity-check against a sheet any more, so a stalled Uniware sync
// has to be visible rather than silently serving yesterday's numbers.
import { computed, ref } from "vue";
import { useSopUcAppData } from "../../composables/useSopUcAppData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const WAREHOUSES = ["Bangalore", "Gurgaon", "Hyderabad", "Mumbai", "Kolkata"];
const DARK_STORE_CITIES = ["DTDC Bangalore", "DTDC Gurgaon", "DTDC Kolkata", "SFX Mumbai", "SFX Hyderabad"];
const VIEWS = [
  { key: "on-hand", label: "On hand Inventory" },
  { key: "in-transit", label: "In-transit" },
  { key: "drr-doi", label: "DRR / DOI" },
];
const IN_TRANSIT_MODES = [
  { key: "in_transit", label: "In-Transit only" },
  { key: "combined", label: "On-Hand + In-Transit" },
];

const { warehouseRows, darkStoreRows, facilityDrrDoiRows, stockSyncedAt, loadError } = useSopUcAppData();

const activeView = ref(VIEWS[0].key);
const inTransitMode = ref(IN_TRANSIT_MODES[0].key);

function fmt(n) {
  return Math.round(n || 0).toLocaleString("en-IN");
}

// "16 Sep, 10:42 pm" in IST -- the sync runs on a UTC cron, so an un-zoned
// render would read hours off to everyone looking at it.
const stockAsOf = computed(() => {
  if (!stockSyncedAt.value) return "";
  const d = new Date(stockSyncedAt.value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-IN", {
    day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
    hour12: true, timeZone: "Asia/Kolkata",
  });
});

function buildMatrix(rowLabels, rowKey, rows, valueKey) {
  const byRow = {};
  for (const label of rowLabels) byRow[label] = Object.fromEntries(SKUS.map(s => [s, 0]));
  for (const r of rows) {
    if (byRow[r[rowKey]] && SKUS.includes(r.sku)) byRow[r[rowKey]][r.sku] = r[valueKey] || 0;
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

const onHandMatrix = computed(() => buildMatrix(WAREHOUSES, "warehouse", warehouseRows.value, "on_hand"));
const inTransitMatrix = computed(() => buildMatrix(WAREHOUSES, "warehouse", warehouseRows.value, inTransitMode.value));

// One table per DTDC/SFX city, listing that city's individual dark stores.
const darkStoreTables = computed(() => DARK_STORE_CITIES.map(city => {
  const cityRows = darkStoreRows.value.filter(r => r.city === city);
  const stores = [...new Set(cityRows.map(r => r.store))].sort();
  return { city, ...buildMatrix(stores, "store", cityRows, "on_hand") };
}));

// DRR/DOI: warehouses first, then all dark stores that have a block (facility names already say
// which city they're in, e.g. "PB-UC-BLR-NERALURU").
const drrDoiTable = computed(() => {
  const byKey = Object.fromEntries(facilityDrrDoiRows.value.map(r => [`${r.facility}|${r.sku}`, r]));
  const darkStoreNames = [...new Set(
    facilityDrrDoiRows.value.filter(r => r.facility_type === "DARK_STORE").map(r => r.facility),
  )].sort();
  return [...WAREHOUSES, ...darkStoreNames].map(name => ({
    name,
    isDarkStore: darkStoreNames.includes(name),
    cells: SKUS.map(s => byKey[`${name}|${s}`]),
  }));
});

// Red-only, per Anish (2026-09-16): colour here means "needs attention" and nothing else -- no
// green at any value, because a healthy DOI doesn't need to shout. Warehouses flag below 15 days,
// dark stores below 10 (they restock far more often, so a lower cover is normal for them).
function doiClass(row) {
  if (!row || row.doi == null) return "";
  const floor = row.facility_type === "DARK_STORE" ? 10 : 15;
  return row.doi < floor ? "cell-critical" : "";
}
function doiText(row) {
  if (!row) return "–";
  return row.doi == null ? "–" : (Math.round(row.doi * 10) / 10).toLocaleString("en-IN");
}

const kpiTiles = computed(() => [
  { label: "UC on-hand", value: fmt(onHandMatrix.value.totalRow.total) },
  { label: "UC in-transit", value: fmt(buildMatrix(WAREHOUSES, "warehouse", warehouseRows.value, "in_transit").totalRow.total) },
  { label: "UC combined", value: fmt(buildMatrix(WAREHOUSES, "warehouse", warehouseRows.value, "combined").totalRow.total) },
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
    <h3 class="section-title">Warehouse on-hand</h3>
    <p v-if="stockAsOf" class="muted-text" style="margin: -8px 0 12px;">
      Live Uniware stock, as of {{ stockAsOf }} IST.
    </p>
    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead><tr><th>Warehouse</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
        <tbody>
          <tr v-for="r in onHandMatrix.body" :key="r.label">
            <td>{{ r.label }}</td>
            <td v-for="(c, i) in r.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
            <td class="num mono"><b>{{ fmt(r.total) }}</b></td>
          </tr>
          <tr class="row-total">
            <td>{{ onHandMatrix.totalRow.label }}</td>
            <td v-for="(c, i) in onHandMatrix.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
            <td class="num mono">{{ fmt(onHandMatrix.totalRow.total) }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>

    <h3 class="section-title">Dark stores on-hand</h3>
    <div v-for="t in darkStoreTables" :key="t.city" class="table-card" style="margin-bottom: 16px;">
      <h4 class="card-caption">{{ t.city }}</h4>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Store</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
          <tbody>
            <tr v-for="r in t.body" :key="r.label">
              <td class="fac-code">{{ r.label }}</td>
              <td v-for="(c, i) in r.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
              <td class="num mono"><b>{{ fmt(r.total) }}</b></td>
            </tr>
            <tr class="row-total">
              <td>{{ t.totalRow.label }}</td>
              <td v-for="(c, i) in t.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
              <td class="num mono">{{ fmt(t.totalRow.total) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <div v-show="activeView === 'in-transit'">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
      <h3 class="section-title" style="margin: 0;">In-transit</h3>
      <div class="radio-pill-group">
        <label v-for="m in IN_TRANSIT_MODES" :key="m.key" class="radio-pill">
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
          <tr class="row-total">
            <td>{{ inTransitMatrix.totalRow.label }}</td>
            <td v-for="(c, i) in inTransitMatrix.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
            <td class="num mono">{{ fmt(inTransitMatrix.totalRow.total) }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </div>

  <div v-show="activeView === 'drr-doi'">
    <h3 class="section-title" style="margin-bottom: 6px;">Warehouse &amp; dark-store DRR / DOI</h3>
    <p class="field-hint" style="margin: 0 0 10px;">
      DRR: trailing 10-day average (warehouses) / 15-day average (dark stores) direct sales. DOI: on-hand / DRR.
      Only low cover is flagged:
      <span class="chip chip-critical" style="margin-left: 6px;">warehouse DOI &lt; 15</span>
      <span class="chip chip-critical" style="margin-left: 4px;">dark store DOI &lt; 10</span>
    </p>
    <div class="table-card"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th rowspan="2">Facility</th>
            <th v-for="s in SKUS" :key="s" colspan="2" class="col-group">{{ s }}</th>
          </tr>
          <tr>
            <template v-for="s in SKUS" :key="s">
              <th class="num-c col-sep">DRR</th>
              <th class="num-c">DOI</th>
            </template>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in drrDoiTable" :key="r.name">
            <td :class="r.isDarkStore ? 'fac-code' : ''"><b v-if="!r.isDarkStore">{{ r.name }}</b><template v-else>{{ r.name }}</template></td>
            <template v-for="(c, i) in r.cells" :key="i">
              <td class="num-c mono col-sep">{{ c ? fmt(c.drr) : "–" }}</td>
              <td class="num-c mono" :class="doiClass(c)">{{ doiText(c) }}</td>
            </template>
          </tr>
        </tbody>
      </table>
    </div></div>
  </div>
</template>
