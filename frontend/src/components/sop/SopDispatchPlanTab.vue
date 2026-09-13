<script setup>
// S&OP > S&OP Planning (formerly labeled "Channel Dispatch Plan" in the sub-tab
// strip) -- 7 rolling horizons + 1 pinned calendar date, each with a channel
// view and an independent warehouse view (see sync_sop_dispatch_plan.py's
// docstring for the no-inter-warehouse-netting invariant this data already
// respects), plus a Production Check panel per DOI target.
//
// Redesigned 2026-09-13 (per Anish: the original two-always-visible-tables x
// 3-stacked-sub-columns-per-SKU layout was too cluttered): a Scope toggle
// (Channel/Warehouse) shows one table at a time, and a metric switcher
// (Required Dispatch / Status / Projected DOI) shows one column per SKU
// instead of three -- same underlying data, just one dimension displayed at
// a time instead of all at once.
import { ref, computed } from "vue";
import { useSopDispatchPlanData } from "../../composables/useSopDispatchPlanData.js";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const CHANNELS = ["UC App + PLS", "Amazon", "Flipkart", "MT"];
const WAREHOUSES = ["Bangalore", "Gurgaon", "Hyderabad", "Mumbai", "Kolkata"];
const VIEW_KEYS = ["+7", "+15", "+21", "+30", "+45", "+60", "+90", "PINNED"];
const DOI_TARGETS = [30, 15, 7, 0];
const SCOPES = [{ key: "CHANNEL", label: "Channel" }, { key: "WAREHOUSE", label: "Warehouse" }];
const METRICS = [
  { key: "required_dispatch", label: "Required Dispatch" },
  { key: "status", label: "Status" },
  { key: "projected_doi", label: "Projected DOI" },
];

const STATUS_CHIP_CLASS = {
  "ON TRACK": "chip-good", "NEEDS DISPATCH": "chip-open", "ALREADY SHORT": "chip-critical",
  "SHORTFALL": "chip-critical", "N/A": "chip-muted",
};

const { planRows, productionCheckRows, runDate, loadError } = useSopDispatchPlanData();

const activeView = ref("+30");
const activeDoi = ref(30);
const activeScope = ref("CHANNEL");
const activeMetric = ref("required_dispatch");

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
const activeTable = computed(() => activeScope.value === "CHANNEL" ? channelTable.value : warehouseTable.value);
const scopeColumnLabel = computed(() => activeScope.value === "CHANNEL" ? "Channel" : "Warehouse");
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

    <div class="panel" style="margin-bottom: 18px;">
      <div style="display: flex; flex-wrap: wrap; gap: 28px;">
        <div>
          <div class="field-hint" style="margin: 0 0 6px;">Scope</div>
          <div style="display: flex; gap: 14px;">
            <label v-for="s in SCOPES" :key="s.key" style="display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 0.85rem;">
              <input type="radio" :value="s.key" v-model="activeScope"> {{ s.label }}
            </label>
          </div>
        </div>
        <div>
          <div class="field-hint" style="margin: 0 0 6px;">DOI target</div>
          <div style="display: flex; gap: 14px;">
            <label v-for="d in DOI_TARGETS" :key="d" style="display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 0.85rem;">
              <input type="radio" :value="d" v-model="activeDoi"> {{ d }}
            </label>
          </div>
        </div>
        <div>
          <div class="field-hint" style="margin: 0 0 6px;">Show</div>
          <div style="display: flex; gap: 14px;">
            <label v-for="m in METRICS" :key="m.key" style="display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 0.85rem;">
              <input type="radio" :value="m.key" v-model="activeMetric"> {{ m.label }}
            </label>
          </div>
        </div>
      </div>
    </div>

    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead><tr><th>{{ scopeColumnLabel }}</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th></tr></thead>
        <tbody>
          <tr v-for="r in activeTable" :key="r.scope">
            <td>{{ r.scope }}</td>
            <td v-for="(c, i) in r.cells" :key="i" class="num">
              <span v-if="!c">&#8211;</span>
              <span v-else-if="activeMetric === 'required_dispatch'" class="mono">{{ fmt(c.required_dispatch) }}</span>
              <span v-else-if="activeMetric === 'status'" class="chip" :class="STATUS_CHIP_CLASS[c.status]">{{ c.status }}</span>
              <template v-else>
                <span v-if="c.projected_doi_flag === 'INSUFFICIENT_DATA'" class="chip chip-muted">insufficient data</span>
                <span v-else class="mono">{{ fmtDoi(c) }}</span>
              </template>
            </td>
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
