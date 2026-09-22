<script setup>
// S&OP > UC App + PLS -- new section (2026-09-15, per Anish) sitting
// between Inventory Overview and Sales: Plan vs Actual. Covers the 5 UC
// warehouses AND, since 2026-09-16, the individual dark stores within each
// DTDC/SFX bucket -- on-hand, in-transit (with a toggle to add on-hand back
// in), and a DOI health view.
//
// Each DTDC/SFX bucket in "Current Inventory" turned out to be an aggregate
// label over multiple individual dark stores (confirmed live 2026-09-16,
// correcting an earlier wrong assumption that only city-aggregated totals
// existed) -- 27 stores as of 2026-09-22, most of which also have their own
// "UC sales trackr" DRR block. A store without one gets an on-hand row but no
// DOI row, so it simply doesn't appear on the DOI card (see
// find_trackr_title_cols in scripts/sop_common.py).
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
const DARK_STORE_CITIES = ["DTDC Bangalore", "DTDC Gurgaon", "DTDC Kolkata", "SFX Mumbai",
  "SFX Hyderabad", "SFX MFCs"];
const VIEWS = [
  { key: "on-hand", label: "On hand Inventory" },
  { key: "in-transit", label: "In-transit" },
  // Key stays "drr-doi" (it's only an internal id); the label dropped "DRR /"
  // on 2026-09-21 when the DRR columns moved to a per-cell tooltip.
  { key: "drr-doi", label: "DOI" },
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

// DOI heatmap: split into two cards (2026-09-21, per Anish) because warehouses and
// dark stores are scored on different bands -- one shared legend would have had to
// describe two scales at once. Facility names already say which city they're in
// (e.g. "PB-UC-BLR-NERALURU"), so neither card needs a city column.
const doiByKey = computed(() =>
  Object.fromEntries(facilityDrrDoiRows.value.map(r => [`${r.facility}|${r.sku}`, r])));

function doiRowsFor(names) {
  return names.map(name => ({ name, cells: SKUS.map(s => doiByKey.value[`${name}|${s}`]) }));
}

const warehouseDoiRows = computed(() => doiRowsFor(WAREHOUSES));
const darkStoreDoiRows = computed(() => doiRowsFor([...new Set(
  facilityDrrDoiRows.value.filter(r => r.facility_type === "DARK_STORE").map(r => r.facility),
)].sort()));

// Green/amber/red bands, per Anish (2026-09-21), replacing the red-only scheme this
// view launched with on 2026-09-16: healthy cover is now stated outright rather than
// implied by the absence of red. Dark stores restock far more often than warehouses,
// so their whole band sits lower (5/10 vs 15/30).
//
// A null DOI (no sales at all in the DRR window, so the ratio is undefined) stays
// deliberately uncoloured: stock with zero recorded demand is infinite cover
// arithmetically, but painting it green would hide a dead SKU behind the best colour
// on the card.
function doiBand(row, isDarkStore) {
  if (!row || row.doi == null) return "";
  const [floor, target] = isDarkStore ? [5, 10] : [15, 30];
  if (row.doi < floor) return "cell-critical";
  if (row.doi <= target) return "cell-open";
  return "cell-good";
}
// Always one decimal, even on a whole number: a column mixing "9" and "23.6" reads
// as two different precisions when it's really one.
function doiText(row) {
  if (!row || row.doi == null) return "–";
  return row.doi.toLocaleString("en-IN", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}
// The DRR that produced this DOI, plus the on-hand it divided -- kept on hover now
// that the DRR columns are gone. One decimal, not fmt()'s integer rounding: plenty of
// per-facility DRRs sit under 1/day and would render as a useless "0/day".
function doiTooltip(row) {
  if (!row) return "";
  const drr = (Math.round((row.drr || 0) * 10) / 10).toLocaleString("en-IN");
  return `DRR ${drr}/day · on-hand ${fmt(row.on_hand)}`;
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
    <h3 class="section-title" style="margin-bottom: 6px;">Days of inventory</h3>
    <p class="field-hint" style="margin: 0 0 14px;">
      DOI: on-hand / DRR, where DRR is a trailing 10-day average (warehouses) or 15-day average
      (dark stores) of that facility's own direct sales. Hover any cell for its DRR and on-hand.
    </p>

    <div class="table-card" style="margin-bottom: 20px;">
      <h4 class="card-caption card-caption-center">UC Warehouses — {{ warehouseDoiRows.length }}</h4>
      <div class="table-scroll">
        <table class="doi-table">
          <thead>
            <tr>
              <th class="doi-facility">Facility</th>
              <th v-for="s in SKUS" :key="s" class="num-c">{{ s }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in warehouseDoiRows" :key="r.name">
              <td class="doi-facility"><b>{{ r.name }}</b></td>
              <td v-for="(c, i) in r.cells" :key="i" class="doi-cell">
                <span class="doi-pill" :class="doiBand(c, false)" :title="doiTooltip(c)">{{ doiText(c) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="card-legend">
        <span class="chip chip-good">&gt; 30</span> healthy
        <span class="chip chip-open">15–30</span> watch
        <span class="chip chip-critical">&lt; 15</span> low
        <span class="legend-note">– = no sales in the window</span>
      </div>
    </div>

    <div class="table-card">
      <h4 class="card-caption card-caption-center">Dark Stores — {{ darkStoreDoiRows.length }}</h4>
      <div class="table-scroll">
        <table class="doi-table">
          <thead>
            <tr>
              <th class="doi-facility">Facility</th>
              <th v-for="s in SKUS" :key="s" class="num-c">{{ s }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in darkStoreDoiRows" :key="r.name">
              <td class="doi-facility fac-code">{{ r.name }}</td>
              <td v-for="(c, i) in r.cells" :key="i" class="doi-cell">
                <span class="doi-pill" :class="doiBand(c, true)" :title="doiTooltip(c)">{{ doiText(c) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="card-legend">
        <span class="chip chip-good">&gt; 10</span> healthy
        <span class="chip chip-open">5–10</span> watch
        <span class="chip chip-critical">&lt; 5</span> low
        <span class="legend-note">– = no sales in the window</span>
      </div>
    </div>
  </div>
</template>
