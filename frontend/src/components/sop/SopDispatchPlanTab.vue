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
// Redesigned 2026-09-13 (Scope/Metric switcher), 2026-09-15 (KPI tiles, real
// pinned-date labels, row tint), 2026-09-16 (dropped both toggles for an
// always-both-visible card grid) and AGAIN 2026-09-16 per Anish, pointing at
// the local /channel-dispatch-plan skill's own --html output a second time
// ("why are you not understanding, the structure be same") -- this rebuild
// matches that reference structure literally: a Scope toggle that REPLACES
// the view (not additive), one full-width section per channel/warehouse
// (not a card grid) with a multi-DOI-target summary line and an on-hand
// caption, full-row status tinting throughout, and a bold TOTAL row per
// table -- confirmed live against the reference this session, including
// that its KPI tiles are computed from channel-scope data only regardless
// of which scope is toggled.
import { ref, computed } from "vue";
import { useSopDispatchPlanData } from "../../composables/useSopDispatchPlanData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const CHANNELS = ["UC App + PLS", "Amazon", "Flipkart", "MT"];
const WAREHOUSES = ["Bangalore", "Gurgaon", "Hyderabad", "Mumbai", "Kolkata"];
const HORIZON_VIEW_KEYS = ["+7", "+15", "+21", "+30", "+45", "+60", "+90"];
const DOI_TARGETS = [30, 15, 7, 0];
const SCOPES = [
  { key: "CHANNEL", label: "By Channel", sub: "(UC App + PLS / Amazon / Flipkart / MT)", names: CHANNELS },
  { key: "WAREHOUSE", label: "By Warehouse", sub: "(UC App + PLS only)", names: WAREHOUSES },
];

const STATUS_CHIP_CLASS = {
  "ON TRACK": "chip-good", "NEEDS DISPATCH": "chip-open", "ALREADY SHORT": "chip-critical",
  "SHORTFALL": "chip-critical", "N/A": "chip-muted",
};
const STATUS_ROW_CLASS = {
  "ON TRACK": "bg-good-soft", "NEEDS DISPATCH": "bg-open-soft",
  "ALREADY SHORT": "bg-critical-soft", "SHORTFALL": "bg-critical-soft",
};
// Worst-first, for a section's rolled-up TOTAL row status.
const STATUS_SEVERITY = ["ALREADY SHORT", "SHORTFALL", "NEEDS DISPATCH", "ON TRACK", "N/A"];

const { planRows, productionCheckRows, runDate, loadError } = useSopDispatchPlanData();

const activeView = ref("+30");
const activeDoi = ref(30);
const activeScope = ref("CHANNEL");

function fmt(n) {
  return n == null ? "–" : Math.round(n).toLocaleString("en-IN");
}
function fmtDoi(row) {
  if (row.projected_doi_flag) return row.projected_doi_flag; // ">60"
  return row.projected_doi == null ? "–" : (Math.round(row.projected_doi * 10) / 10).toLocaleString("en-IN");
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

function rowsForScope(scopeType, doiTarget) {
  return planRows.value.filter(r => r.view_key === activeView.value && r.scope_type === scopeType && r.doi_target === doiTarget);
}

// One section per channel/warehouse in the active scope: SKU rows at the active DOI target, a
// per-DOI-target required-dispatch summary (all 4 targets are already in memory -- no extra
// fetch), an on-hand caption (on_hand doesn't vary by target, so any target's rows will do), and a
// rolled-up total row.
function sectionsFor(scopeType, names) {
  const rowsAtActiveDoi = rowsForScope(scopeType, activeDoi.value);
  return names.map(name => {
    const cells = SKUS.map(sku => rowsAtActiveDoi.find(r => r.scope === name && r.sku === sku) || null);
    const requiredByDoi = Object.fromEntries(DOI_TARGETS.map(d => {
      const rows = rowsForScope(scopeType, d).filter(r => r.scope === name);
      return [d, rows.reduce((sum, r) => sum + (r.required_dispatch || 0), 0)];
    }));
    const present = cells.filter(Boolean);
    const total = {
      on_hand: present.reduce((s, c) => s + (c.on_hand || 0), 0),
      po_out: present.reduce((s, c) => s + (c.po_out || 0), 0),
      sales_expected: present.reduce((s, c) => s + (c.sales_expected || 0), 0),
      projected_closing: present.reduce((s, c) => s + (c.projected_closing || 0), 0),
      target_closing: present.reduce((s, c) => s + (c.target_closing || 0), 0),
      required_dispatch: present.reduce((s, c) => s + (c.required_dispatch || 0), 0),
      status: present.length
        ? STATUS_SEVERITY.find(s => present.some(c => c.status === s)) || "N/A"
        : "N/A",
    };
    return { name, cells, requiredByDoi, total };
  });
}
const activeScopeConfig = computed(() => SCOPES.find(s => s.key === activeScope.value));
const activeSections = computed(() => sectionsFor(activeScopeConfig.value.key, activeScopeConfig.value.names));

// KPI tiles always reflect the CHANNEL scope, regardless of which scope is toggled -- matches the
// reference dashboard's own behavior (confirmed live: its tiles don't move when switching to the
// Warehouse view).
const kpiTiles = computed(() => {
  const channelSections = sectionsFor("CHANNEL", CHANNELS);
  const counts = { "ON TRACK": 0, "NEEDS DISPATCH": 0, "ALREADY SHORT": 0 };
  let totalRequired = 0;
  for (const section of channelSections) {
    for (const c of section.cells) {
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

const productionCheckForView = computed(() => productionCheckRows.value.filter(r => r.view_key === activeView.value));
const productionCheckTotal = computed(() => {
  const rows = productionCheckForView.value;
  if (!rows.length) return null;
  return {
    production_planned: rows.reduce((s, r) => s + (r.production_planned || 0), 0),
    required: rows.reduce((s, r) => s + (r.required || 0), 0),
    gap: rows.reduce((s, r) => s + (r.gap || 0), 0),
    status: STATUS_SEVERITY.find(s => rows.some(r => r.status === s)) || "N/A",
  };
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

    <div class="panel" style="margin-bottom: 14px; display: flex; flex-wrap: wrap; gap: 8px;">
      <button
        v-for="s in SCOPES" :key="s.key"
        class="radio-pill" style="padding: 6px 12px; border-radius: 7px; border: 1px solid var(--line); background: var(--surface);"
        :style="activeScope === s.key ? { background: 'var(--accent)', color: 'var(--surface)', borderColor: 'var(--accent)' } : {}"
        @click="activeScope = s.key"
      >
        <b>{{ s.label }}</b> <span style="opacity: 0.75;">{{ s.sub }}</span>
      </button>
    </div>

    <div class="panel" style="margin-bottom: 18px; display: flex; align-items: center; gap: 14px;">
      <div class="field-hint" style="margin: 0;">DOI target</div>
      <div class="radio-pill-group">
        <label v-for="d in DOI_TARGETS" :key="d" class="radio-pill">
          <input type="radio" :value="d" v-model="activeDoi"> {{ d }}
        </label>
      </div>
    </div>

    <div v-for="section in activeSections" :key="section.name" style="margin-bottom: 22px;">
      <p style="font-size: 0.85rem; font-weight: 600; margin: 0 0 4px; text-transform: uppercase; letter-spacing: 0.02em;">
        {{ section.name }} — Total required dispatch:
        <span v-for="(d, i) in DOI_TARGETS" :key="d" class="mono">
          {{ fmt(section.requiredByDoi[d]) }} ({{ d }} DOI)<template v-if="i < DOI_TARGETS.length - 1"> / </template>
        </span>
      </p>
      <p class="field-hint" style="margin: 0 0 10px; font-style: italic;">
        SKU wise inventory on hand on {{ section.name }}:
        <span v-for="(sku, i) in SKUS" :key="sku" class="mono">{{ sku }}={{ fmt(section.cells[i] ? section.cells[i].on_hand : 0) }}<template v-if="i < SKUS.length - 1">&nbsp;</template></span>
      </p>
      <div class="table-card"><div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>SKU</th><th class="num">On-Hand</th><th class="num">PO</th><th class="num">Sales Exp.</th>
              <th class="num">Proj. Closing</th><th class="num">Target Closing</th><th class="num">Req. Dispatch</th>
              <th>Status</th><th class="num">Proj. DOI</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(c, i) in section.cells" :key="i" :class="c ? STATUS_ROW_CLASS[c.status] : ''">
              <td>{{ SKUS[i] }}</td>
              <template v-if="c">
                <td class="num mono">{{ fmt(c.on_hand) }}</td>
                <td class="num mono">{{ fmt(c.po_out) }}</td>
                <td class="num mono">{{ fmt(c.sales_expected) }}</td>
                <td class="num mono">{{ fmt(c.projected_closing) }}</td>
                <td class="num mono">{{ fmt(c.target_closing) }}</td>
                <td class="num mono">{{ fmt(c.required_dispatch) }}</td>
                <td><span class="chip" :class="STATUS_CHIP_CLASS[c.status]">{{ c.status }}</span></td>
                <td class="num mono">{{ fmtDoi(c) }}</td>
              </template>
              <template v-else><td colspan="8">&#8211;</td></template>
            </tr>
            <tr class="row-total">
              <td>Total</td>
              <td class="num mono">{{ fmt(section.total.on_hand) }}</td>
              <td class="num mono">{{ fmt(section.total.po_out) }}</td>
              <td class="num mono">{{ fmt(section.total.sales_expected) }}</td>
              <td class="num mono">{{ fmt(section.total.projected_closing) }}</td>
              <td class="num mono">{{ fmt(section.total.target_closing) }}</td>
              <td class="num mono">{{ fmt(section.total.required_dispatch) }}</td>
              <td><span class="chip" :class="STATUS_CHIP_CLASS[section.total.status]">{{ section.total.status }}</span></td>
              <td class="num mono">&#8211;</td>
            </tr>
          </tbody>
        </table>
      </div></div>
    </div>

    <h3 class="section-title">Production check (this DOI target)</h3>
    <div class="table-card"><div class="table-scroll">
      <table>
        <thead><tr><th>SKU</th><th class="num">Planned</th><th class="num">Required</th><th class="num">Gap</th><th>Status</th></tr></thead>
        <tbody>
          <tr v-for="r in productionCheckForView" :key="r.sku" :class="STATUS_ROW_CLASS[r.status] || ''">
            <td>{{ r.sku }}</td>
            <td class="num mono">{{ fmt(r.production_planned) }}</td>
            <td class="num mono">{{ fmt(r.required) }}</td>
            <td class="num mono">{{ fmt(r.gap) }}</td>
            <td><span class="chip" :class="STATUS_CHIP_CLASS[r.status]">{{ r.status }}</span></td>
          </tr>
          <tr v-if="productionCheckTotal" class="row-total">
            <td>Total</td>
            <td class="num mono">{{ fmt(productionCheckTotal.production_planned) }}</td>
            <td class="num mono">{{ fmt(productionCheckTotal.required) }}</td>
            <td class="num mono">{{ fmt(productionCheckTotal.gap) }}</td>
            <td><span class="chip" :class="STATUS_CHIP_CLASS[productionCheckTotal.status]">{{ productionCheckTotal.status }}</span></td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
