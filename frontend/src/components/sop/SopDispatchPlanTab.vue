<script setup>
// S&OP > S&OP Planning (formerly labeled "Channel Dispatch Plan" in the sub-tab
// strip) -- 7 rolling horizons + however many pinned calendar dates are
// currently configured (see sop_dispatch_pinned_date -- the business can have
// more than one active at once, e.g. 24-Sep-2026 and 30-Sep-2026 coexisted
// starting 2026-09-15), each with a channel view and an independent
// warehouse view (see sync_sop_dispatch_plan.py's docstring for the
// no-inter-warehouse-netting invariant this data already respects), plus a
// Production Check panel per DOI target.
//
// Redesigned 2026-09-13 (Scope/Metric switcher, replacing an 18-column-per-
// table layout), 2026-09-15 (KPI tiles, real pinned-date labels, row tint),
// and again 2026-09-16 per Anish ("make it similar to the /channel-dispatch-
// plan skill's view, with the color coding as well") -- dropped both
// toggles entirely: a Channel view (4 cards) and a Warehouse view (5 cards)
// are now always both visible, one small SKU-rows table per card showing
// every column at once, every row tinted by its own status (not just
// ALREADY SHORT) using the portal's own --good-soft/--open-soft/
// --critical-soft tokens (see .bg-*-soft in shared.css).
import { ref, computed } from "vue";
import { useSopDispatchPlanData } from "../../composables/useSopDispatchPlanData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const CHANNELS = ["UC App + PLS", "Amazon", "Flipkart", "MT"];
const WAREHOUSES = ["Bangalore", "Gurgaon", "Hyderabad", "Mumbai", "Kolkata"];
const HORIZON_VIEW_KEYS = ["+7", "+15", "+21", "+30", "+45", "+60", "+90"];
const DOI_TARGETS = [30, 15, 7, 0];

const STATUS_CHIP_CLASS = {
  "ON TRACK": "chip-good", "NEEDS DISPATCH": "chip-open", "ALREADY SHORT": "chip-critical",
  "SHORTFALL": "chip-critical", "N/A": "chip-muted",
};
const STATUS_ROW_CLASS = {
  "ON TRACK": "bg-good-soft", "NEEDS DISPATCH": "bg-open-soft", "ALREADY SHORT": "bg-critical-soft",
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
function pinnedLabel(ymd) {
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "UTC" });
}

// The horizon views are always offered (even before any data has landed); pinned views are
// discovered from whatever PINNED_<ymd> view_keys are actually present in synced data, sorted by
// date, so this automatically tracks however many pinned dates are currently configured.
const availableViews = computed(() => {
  const present = new Set(planRows.value.map(r => r.view_key));
  const pinned = [...present].filter(v => v.startsWith("PINNED_")).sort();
  return [
    ...HORIZON_VIEW_KEYS.filter(v => present.has(v)),
    ...pinned,
  ];
});
function viewLabel(v) {
  return v.startsWith("PINNED_") ? pinnedLabel(v.slice("PINNED_".length)) : v;
}

function rowsFor(scopeType) {
  return planRows.value.filter(r => r.view_key === activeView.value && r.scope_type === scopeType && r.doi_target === activeDoi.value);
}

// One card per channel/warehouse: a small SKU-rows table with every column shown at once (no more
// Scope/Metric toggle hiding data behind a click).
function cardsFor(scopeType, scopeNames) {
  const rows = rowsFor(scopeType);
  return scopeNames.map(name => ({
    scope: name,
    rows: SKUS.map(sku => rows.find(r => r.scope === name && r.sku === sku) || null),
  }));
}
const channelCards = computed(() => cardsFor("CHANNEL", CHANNELS));
const warehouseCards = computed(() => cardsFor("WAREHOUSE", WAREHOUSES));

const kpiTiles = computed(() => {
  const counts = { "ON TRACK": 0, "NEEDS DISPATCH": 0, "ALREADY SHORT": 0 };
  let totalRequired = 0;
  for (const card of [...channelCards.value, ...warehouseCards.value]) {
    for (const c of card.rows) {
      if (!c) continue;
      if (counts[c.status] !== undefined) counts[c.status]++;
      totalRequired += c.required_dispatch || 0;
    }
  }
  const productionShortfalls = productionCheckRows.value.filter(
    r => r.view_key === activeView.value && r.status === "SHORTFALL",
  ).length;
  const tiles = [
    { label: "On track", value: fmt(counts["ON TRACK"]), cls: "good" },
    { label: "Needs dispatch", value: fmt(counts["NEEDS DISPATCH"]) },
    { label: "Already short", value: fmt(counts["ALREADY SHORT"]), cls: counts["ALREADY SHORT"] > 0 ? "critical" : undefined },
    { label: "Total units needed", value: fmt(totalRequired) },
  ];
  if (productionShortfalls > 0) {
    tiles.push({ label: "Production shortfall", value: fmt(productionShortfalls), cls: "critical" });
  }
  return tiles;
});
</script>

<template>
  <div v-if="loadError" class="form-error">{{ loadError }}</div>
  <div v-if="!runDate" class="empty-state">No dispatch plan run yet -- the sync workflow hasn't populated this table.</div>

  <template v-else>
    <SummaryKpis :tiles="kpiTiles" />

    <div class="subtabs" style="margin-bottom: 14px;">
      <button v-for="v in availableViews" :key="v" class="subtab-item" :class="{ active: activeView === v }" @click="activeView = v">
        {{ viewLabel(v) }}<template v-if="v.startsWith('PINNED_')"> (pinned)</template>
      </button>
    </div>
    <p v-if="activeView.startsWith('PINNED_')" class="chip chip-muted" style="display: inline-block; margin-bottom: 14px;">
      Amazon/Flipkart/MT's Target Closing on this view is overridden by the Diwali Sales Plan tab's Opening Ask.
    </p>

    <div class="panel" style="margin-bottom: 18px; display: flex; align-items: center; gap: 14px;">
      <div class="field-hint" style="margin: 0;">DOI target</div>
      <div style="display: flex; gap: 14px;">
        <label v-for="d in DOI_TARGETS" :key="d" style="display: flex; align-items: center; gap: 5px; cursor: pointer; font-size: 0.85rem;">
          <input type="radio" :value="d" v-model="activeDoi"> {{ d }}
        </label>
      </div>
    </div>

    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Channel view</h3>
    <div class="dispatch-card-grid" style="margin-bottom: 28px;">
      <div v-for="card in channelCards" :key="card.scope" class="table-card">
        <h4 style="font-size: 0.85rem; margin: 0; padding: 10px 12px; border-bottom: 1px solid var(--line);">{{ card.scope }}</h4>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>SKU</th><th class="num">On-Hand</th><th class="num">PO</th><th class="num">Sales Exp.</th>
                <th class="num">Proj. Closing</th><th class="num">Target Closing</th><th class="num">Req. Dispatch</th>
                <th>Status</th><th class="num">Proj. DOI</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(c, i) in card.rows" :key="i" :class="c ? STATUS_ROW_CLASS[c.status] : ''">
                <td>{{ SKUS[i] }}</td>
                <template v-if="c">
                  <td class="num mono">{{ fmt(c.on_hand) }}</td>
                  <td class="num mono">{{ fmt(c.po_out) }}</td>
                  <td class="num mono">{{ fmt(c.sales_expected) }}</td>
                  <td class="num mono">{{ fmt(c.projected_closing) }}</td>
                  <td class="num mono">{{ fmt(c.target_closing) }}</td>
                  <td class="num mono"><b>{{ fmt(c.required_dispatch) }}</b></td>
                  <td><span class="chip" :class="STATUS_CHIP_CLASS[c.status]">{{ c.status }}</span></td>
                  <td class="num mono">
                    <span v-if="c.projected_doi_flag === 'INSUFFICIENT_DATA'" class="chip chip-muted">n/a</span>
                    <span v-else>{{ fmtDoi(c) }}</span>
                  </td>
                </template>
                <template v-else><td colspan="8">&#8211;</td></template>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Warehouse view</h3>
    <div class="dispatch-card-grid" style="margin-bottom: 24px;">
      <div v-for="card in warehouseCards" :key="card.scope" class="table-card">
        <h4 style="font-size: 0.85rem; margin: 0; padding: 10px 12px; border-bottom: 1px solid var(--line);">{{ card.scope }}</h4>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>SKU</th><th class="num">On-Hand</th><th class="num">PO</th><th class="num">Sales Exp.</th>
                <th class="num">Proj. Closing</th><th class="num">Target Closing</th><th class="num">Req. Dispatch</th>
                <th>Status</th><th class="num">Proj. DOI</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(c, i) in card.rows" :key="i" :class="c ? STATUS_ROW_CLASS[c.status] : ''">
                <td>{{ SKUS[i] }}</td>
                <template v-if="c">
                  <td class="num mono">{{ fmt(c.on_hand) }}</td>
                  <td class="num mono">{{ fmt(c.po_out) }}</td>
                  <td class="num mono">{{ fmt(c.sales_expected) }}</td>
                  <td class="num mono">{{ fmt(c.projected_closing) }}</td>
                  <td class="num mono">{{ fmt(c.target_closing) }}</td>
                  <td class="num mono"><b>{{ fmt(c.required_dispatch) }}</b></td>
                  <td><span class="chip" :class="STATUS_CHIP_CLASS[c.status]">{{ c.status }}</span></td>
                  <td class="num mono">
                    <span v-if="c.projected_doi_flag === 'INSUFFICIENT_DATA'" class="chip chip-muted">n/a</span>
                    <span v-else>{{ fmtDoi(c) }}</span>
                  </td>
                </template>
                <template v-else><td colspan="8">&#8211;</td></template>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

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
