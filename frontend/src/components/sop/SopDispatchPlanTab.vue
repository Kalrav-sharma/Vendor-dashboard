<script setup>
// S&OP > Channel Dispatch Plan -- 7 rolling horizons + 1 pinned calendar
// date, each with a channel view and an independent warehouse view (see
// sync_sop_dispatch_plan.py's docstring for the no-inter-warehouse-
// netting invariant this data already respects), plus a Production Check
// panel per DOI target.
import { ref, computed } from "vue";
import { useSopDispatchPlanData } from "../../composables/useSopDispatchPlanData.js";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const CHANNELS = ["UC App + PLS", "Amazon", "Flipkart", "MT"];
const WAREHOUSES = ["Bangalore", "Gurgaon", "Hyderabad", "Mumbai", "Kolkata"];
const VIEW_KEYS = ["+7", "+15", "+21", "+30", "+45", "+60", "+90", "PINNED"];
const DOI_TARGETS = [30, 15, 7, 0];

const STATUS_CHIP_CLASS = {
  "ON TRACK": "chip-good", "NEEDS DISPATCH": "chip-open", "ALREADY SHORT": "chip-critical",
  "SHORTFALL": "chip-critical", "N/A": "chip-muted",
};

const { planRows, productionCheckRows, runDate, loadError } = useSopDispatchPlanData();

const activeView = ref("+30");
const activeDoi = ref(30);

function fmt(n) {
  return n == null ? "–" : Math.round(n).toLocaleString("en-IN");
}
function fmtDoi(row) {
  if (row.projected_doi_flag === "INSUFFICIENT_DATA") return null; // rendered as a chip instead
  return row.projected_doi == null ? "400+" : (Math.round(row.projected_doi * 10) / 10).toLocaleString("en-IN");
}

const availableViews = computed(() => {
  const present = new Set(planRows.value.map(r => r.view_key));
  return VIEW_KEYS.filter(v => present.has(v));
});

function rowsFor(scopeType) {
  return planRows.value.filter(r => r.view_key === activeView.value && r.scope_type === scopeType && r.doi_target === activeDoi.value);
}

const channelTable = computed(() => {
  const rows = rowsFor("CHANNEL");
  return CHANNELS.map(ch => {
    const bySku = Object.fromEntries(rows.filter(r => r.scope === ch).map(r => [r.sku, r]));
    return { scope: ch, cells: SKUS.map(s => bySku[s]) };
  });
});
const warehouseTable = computed(() => {
  const rows = rowsFor("WAREHOUSE");
  return WAREHOUSES.map(wh => {
    const bySku = Object.fromEntries(rows.filter(r => r.scope === wh).map(r => [r.sku, r]));
    return { scope: wh, cells: SKUS.map(s => bySku[s]) };
  });
});
</script>

<template>
  <div v-if="loadError" class="form-error">{{ loadError }}</div>
  <div v-if="!runDate" class="empty-state">No dispatch plan run yet -- the sync workflow hasn't populated this table.</div>

  <template v-else>
    <div class="subtabs" style="margin-bottom: 14px;">
      <button v-for="v in availableViews" :key="v" class="subtab-item" :class="{ active: activeView === v }" @click="activeView = v">
        {{ v === "PINNED" ? "Pinned date" : v }}
      </button>
    </div>
    <p v-if="activeView === 'PINNED'" class="chip chip-muted" style="display: inline-block; margin-bottom: 14px;">
      Amazon/Flipkart/MT's Target Closing on this view is overridden by the Diwali Sales Plan tab's Opening Ask.
    </p>

    <div class="panel-grid" style="grid-template-columns: repeat(4, auto); margin-bottom: 18px;">
      <label v-for="d in DOI_TARGETS" :key="d" style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
        <input type="radio" :value="d" v-model="activeDoi"> {{ d }}-DOI target
      </label>
    </div>

    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Channel view</h3>
    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead><tr><th>Channel</th><th v-for="s in SKUS" :key="s" colspan="3" class="num">{{ s }}</th></tr>
        <tr><th></th><template v-for="s in SKUS" :key="s+'sub'"><th class="num">Req. dispatch</th><th class="num">Status</th><th class="num">Proj. DOI</th></template></tr></thead>
        <tbody>
          <tr v-for="r in channelTable" :key="r.scope">
            <td>{{ r.scope }}</td>
            <template v-for="(c, i) in r.cells" :key="i">
              <td class="num mono">{{ c ? fmt(c.required_dispatch) : "–" }}</td>
              <td class="num"><span v-if="c" class="chip" :class="STATUS_CHIP_CLASS[c.status]">{{ c.status }}</span></td>
              <td class="num mono">
                <span v-if="c && c.projected_doi_flag === 'INSUFFICIENT_DATA'" class="chip chip-muted">insufficient data</span>
                <template v-else-if="c">{{ fmtDoi(c) }}</template>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div></div>

    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Warehouse view (UC App + PLS split by city)</h3>
    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead><tr><th>Warehouse</th><th v-for="s in SKUS" :key="s" colspan="3" class="num">{{ s }}</th></tr>
        <tr><th></th><template v-for="s in SKUS" :key="s+'sub'"><th class="num">Req. dispatch</th><th class="num">Status</th><th class="num">Proj. DOI</th></template></tr></thead>
        <tbody>
          <tr v-for="r in warehouseTable" :key="r.scope">
            <td>{{ r.scope }}</td>
            <template v-for="(c, i) in r.cells" :key="i">
              <td class="num mono">{{ c ? fmt(c.required_dispatch) : "–" }}</td>
              <td class="num"><span v-if="c" class="chip" :class="STATUS_CHIP_CLASS[c.status]">{{ c.status }}</span></td>
              <td class="num mono">
                <span v-if="c && c.projected_doi_flag === 'INSUFFICIENT_DATA'" class="chip chip-muted">insufficient data</span>
                <template v-else-if="c">{{ fmtDoi(c) }}</template>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div></div>

    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Production check (this DOI target)</h3>
    <div class="table-card"><div class="table-scroll">
      <table>
        <thead><tr><th>SKU</th><th class="num">Planned</th><th class="num">Required</th><th class="num">Gap</th><th>Status</th></tr></thead>
        <tbody>
          <tr v-for="r in productionCheckRows.filter(r => r.view_key === activeView)" :key="r.sku">
            <td>{{ r.sku }}</td>
            <td class="num mono">{{ fmt(r.production_planned) }}</td>
            <td class="num mono">{{ fmt(r.required) }}</td>
            <td class="num mono" :class="{ critical: r.gap < 0 }">{{ fmt(r.gap) }}</td>
            <td><span class="chip" :class="STATUS_CHIP_CLASS[r.status]">{{ r.status }}</span></td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
