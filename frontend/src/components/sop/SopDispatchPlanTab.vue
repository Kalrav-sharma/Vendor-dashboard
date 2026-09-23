<script setup>
// S&OP > S&OP Planning -- 7 rolling horizons + however many pinned calendar
// dates are configured (sop_dispatch_pinned_date; the business can have more
// than one live at once, e.g. 24-Sep-2026 and 30-Sep-2026), each with a
// channel view and an independent warehouse view (see
// sync_sop_dispatch_plan.py's docstring for the no-inter-warehouse-netting
// invariant this data already respects), plus a Production Check panel.
//
// Structure is a deliberate match of the local /channel-dispatch-plan skill's
// own --html report, which Anish asked for three times. Studied live and
// mirrored here: horizon/pinned buttons carrying their resolved target date,
// a window + channel-share context box, one full-width section per
// channel/warehouse, per-scope column labels (On-Hand vs On-Hand + In
// Transit, PO Inflow vs PO Outflow, Plan/day vs DRR/day), headers that name
// the active DOI target and window length, full-row status tinting, a bold
// TOTAL row, and -- on pinned views only -- the note that Amazon/Flipkart/MT
// Target Closing is a live-read committed number from the Diwali tab.
//
// Plan/day and the window length are derived here rather than stored: the
// reference computes Plan/day as sales_expected / window-days (verified
// against it: UC M0 698/31 = 22.5; pinned 30-Sept 320/16 = 20).
import { ref, computed } from "vue";
import { useSopDispatchPlanData } from "../../composables/useSopDispatchPlanData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const CHANNELS = ["UC App + PLS", "Amazon", "Flipkart", "MT"];
const WAREHOUSES = ["Bangalore", "Gurgaon", "Hyderabad", "Mumbai", "Kolkata"];
const HORIZON_VIEW_KEYS = ["+7", "+15", "+21", "+30", "+45", "+60", "+90"];
const DOI_TARGETS = [30, 15, 7, 0];
const SCOPES = [
  { key: "CHANNEL", label: "By Channel", sub: "UC App + PLS / Amazon / Flipkart / MT", names: CHANNELS },
  { key: "WAREHOUSE", label: "By Warehouse", sub: "UC App + PLS only", names: WAREHOUSES },
];
// Scopes whose On-Hand already includes in-transit stock and whose PO figure is an outflow --
// same split the reference report's own column labels make.
const INCLUDES_IN_TRANSIT = new Set(["UC App + PLS", ...WAREHOUSES]);

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
function fmtRate(n) {
  return n == null ? "–" : (Math.round(n * 10) / 10).toLocaleString("en-IN");
}
function fmtDoi(row) {
  if (row.projected_doi_flag) return row.projected_doi_flag; // ">60" | "insufficient data"
  return row.projected_doi == null ? "–" : (Math.round(row.projected_doi * 10) / 10).toLocaleString("en-IN");
}
function addDaysYMD(ymd, days) {
  const [y, m, d] = ymd.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}
function daysBetween(a, b) {
  return Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / 86400000);
}
// "30-Sept-2026", matching the reference report's own date format.
function dateLabel(ymd) {
  if (!ymd) return "";
  const [y, m, d] = ymd.split("-").map(Number);
  const mon = new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" });
  return `${String(d).padStart(2, "0")}-${mon}-${y}`;
}
// UC App + PLS's status is the worst of its 5 warehouses, so name the one(s) driving it -- otherwise
// ALREADY SHORT next to a healthy pooled Proj. Closing reads like a bug (skill change, 2026-09-22).
function statusLabel(c) {
  return c.worst_warehouses ? `${c.status} (${c.worst_warehouses})` : c.status;
}
function isPinned(v) {
  return v.startsWith("PINNED_");
}
// The calendar date a view is planning toward.
function targetDateFor(v) {
  if (!runDate.value) return null;
  return isPinned(v) ? v.slice("PINNED_".length) : addDaysYMD(runDate.value, Number(v));
}

// The horizon views are always offered (even before data lands); pinned views are discovered from
// whatever PINNED_<ymd> view_keys are actually present, so this tracks however many are configured.
const availableViews = computed(() => {
  const present = new Set(planRows.value.map(r => r.view_key));
  const pinned = [...present].filter(isPinned).sort();
  return [...HORIZON_VIEW_KEYS.filter(v => present.has(v)), ...pinned];
});
function viewLabel(v) {
  return isPinned(v) ? dateLabel(v.slice("PINNED_".length)) : `${v}d`;
}
function viewSubLabel(v) {
  return isPinned(v) ? "📌 Fixed" : dateLabel(targetDateFor(v));
}

// Window length in days, and the inclusive day-count the reference prints in its "Sales Exp (Nd)"
// header (and divides by for Plan/day) -- one more than the span, since both ends count.
const windowSpanDays = computed(() => {
  const target = targetDateFor(activeView.value);
  if (!runDate.value || !target) return 0;
  return Math.max(0, daysBetween(runDate.value, target));
});
const windowDays = computed(() => windowSpanDays.value + 1);

function rowsForScope(scopeType, doiTarget) {
  return planRows.value.filter(r => r.view_key === activeView.value && r.scope_type === scopeType && r.doi_target === doiTarget);
}

// One section per channel/warehouse in the active scope: SKU rows at the active DOI target, a
// per-DOI-target required-dispatch summary (all 4 targets are already in memory -- no extra
// fetch), an on-hand caption, and a rolled-up total row.
function sectionsFor(scopeType, names) {
  const rowsAtActiveDoi = rowsForScope(scopeType, activeDoi.value);
  return names.map(name => {
    const cells = SKUS.map(sku => rowsAtActiveDoi.find(r => r.scope === name && r.sku === sku) || null);
    const requiredByDoi = Object.fromEntries(DOI_TARGETS.map(d => {
      const rows = rowsForScope(scopeType, d).filter(r => r.scope === name);
      return [d, rows.reduce((sum, r) => sum + (r.required_dispatch || 0), 0)];
    }));
    const present = cells.filter(Boolean);
    const sum = key => present.reduce((s, c) => s + (c[key] || 0), 0);
    return {
      name,
      cells,
      requiredByDoi,
      includesInTransit: INCLUDES_IN_TRANSIT.has(name),
      total: {
        on_hand: sum("on_hand"), po_out: sum("po_out"), sales_expected: sum("sales_expected"),
        projected_closing: sum("projected_closing"), target_closing: sum("target_closing"),
        required_dispatch: sum("required_dispatch"),
        status: present.length ? (STATUS_SEVERITY.find(s => present.some(c => c.status === s)) || "N/A") : "N/A",
      },
    };
  });
}
const activeScopeConfig = computed(() => SCOPES.find(s => s.key === activeScope.value));
const activeSections = computed(() => sectionsFor(activeScopeConfig.value.key, activeScopeConfig.value.names));

// Each channel's share of the window's total sales-expected, as the reference prints it. Always
// channel-based, like the KPI tiles, regardless of which scope is toggled.
const channelShare = computed(() => {
  const rows = rowsForScope("CHANNEL", activeDoi.value);
  const byChannel = CHANNELS.map(ch => ({
    channel: ch,
    expected: rows.filter(r => r.scope === ch).reduce((s, r) => s + (r.sales_expected || 0), 0),
  }));
  const total = byChannel.reduce((s, c) => s + c.expected, 0);
  if (!total) return [];
  return byChannel.map(c => ({ channel: c.channel, pct: (c.expected / total) * 100 }));
});

// KPI tiles always reflect the CHANNEL scope regardless of which scope is toggled -- matches the
// reference dashboard's own behaviour (confirmed live: its tiles don't move on the Warehouse view).
// Required/gap/status here are computed per DOI target, so this must track the DOI toggle just
// like the plan tables above do -- otherwise all four targets' rows render stacked.
const productionCheckForView = computed(() => {
  const forView = productionCheckRows.value.filter(r => r.view_key === activeView.value);
  const scoped = forView.filter(r => r.doi_target === activeDoi.value);
  if (scoped.length) return scoped;
  // Rows written before doi_target existed carry null, which would otherwise render an empty
  // table between the column being added and the next dispatch-plan sync backfilling it. Fall
  // back to one row per SKU so the panel still says something truthful in that window.
  const seen = new Set();
  return forView.filter(r => r.doi_target == null && !seen.has(r.sku) && seen.add(r.sku));
});
// True while we're showing that fallback, so the panel can say the figures aren't DOI-specific yet.
const productionCheckIsLegacy = computed(() =>
  productionCheckForView.value.length > 0 && productionCheckForView.value[0].doi_target == null,
);

const kpiTiles = computed(() => {
  const counts = { "ON TRACK": 0, "NEEDS DISPATCH": 0, "ALREADY SHORT": 0 };
  let totalRequired = 0;
  for (const section of sectionsFor("CHANNEL", CHANNELS)) {
    for (const c of section.cells) {
      if (!c) continue;
      if (counts[c.status] !== undefined) counts[c.status]++;
      totalRequired += c.required_dispatch || 0;
    }
  }
  const productionShortfalls = productionCheckForView.value.filter(r => r.status === "SHORTFALL").length;
  const tiles = [
    { label: `Total units to dispatch (${activeDoi.value} DOI)`, value: fmt(totalRequired) },
    { label: "Already short", value: fmt(counts["ALREADY SHORT"]), cls: counts["ALREADY SHORT"] > 0 ? "critical" : undefined },
    { label: "Needs dispatch", value: fmt(counts["NEEDS DISPATCH"]) },
    { label: "On track", value: fmt(counts["ON TRACK"]), cls: "good" },
  ];
  if (productionShortfalls > 0) {
    tiles.push({ label: "Production shortfall", value: fmt(productionShortfalls), cls: "critical" });
  }
  return tiles;
});

const productionCheckTotal = computed(() => {
  const rows = productionCheckForView.value;
  if (!rows.length) return null;
  return {
    on_hand_in_transit: rows.reduce((s, r) => s + (r.on_hand_in_transit || 0), 0),
    production_planned: rows.reduce((s, r) => s + (r.production_planned || 0), 0),
    total_available: rows.reduce((s, r) => s + (r.total_available || 0), 0),
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
    <div class="subtabs" style="margin-bottom: 14px;">
      <button v-for="v in availableViews" :key="v" class="subtab-item" :class="{ active: activeView === v }" @click="activeView = v">
        {{ viewLabel(v) }}<br><span class="subtab-sub">{{ viewSubLabel(v) }}</span>
      </button>
    </div>

    <div class="panel" style="margin-bottom: 14px;">
      <p style="margin: 0 0 6px; font-size: 0.83rem;">
        <b>Window:</b> {{ dateLabel(runDate) }} → <b>{{ dateLabel(targetDateFor(activeView)) }}</b>
        ({{ windowSpanDays }} days) &middot; sales-expected and targets come from each channel's own
        daily-forecast tab &mdash; no blending.
      </p>
      <p v-if="channelShare.length" style="margin: 0; font-size: 0.83rem; color: var(--muted);">
        <b>Channel share of sales expected this window:</b>&nbsp;
        <span v-for="(c, i) in channelShare" :key="c.channel">
          {{ c.channel }} {{ c.pct.toFixed(1) }}%<template v-if="i < channelShare.length - 1">&nbsp;&middot;&nbsp;</template>
        </span>
      </p>
      <p v-if="isPinned(activeView)" style="margin: 6px 0 0; font-size: 0.83rem;">
        📌 <b>Fixed date.</b> Amazon/Flipkart/MT Target Closing on this view is the committed Opening
        Ask read live from the Diwali Sales Plan tab, re-checked every run.
      </p>
    </div>

    <SummaryKpis :tiles="kpiTiles" />

    <div class="panel" style="margin-bottom: 14px; display: flex; flex-wrap: wrap; gap: 8px;">
      <button
        v-for="s in SCOPES" :key="s.key"
        class="toggle-btn" :class="{ active: activeScope === s.key }"
        @click="activeScope = s.key"
      >
        <b>{{ s.label }}</b> <span class="sub">({{ s.sub }})</span>
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

    <p v-if="activeScope === 'WAREHOUSE'" class="field-hint" style="margin: 0 0 12px; font-style: italic;">
      Warehouse view of UC App + PLS only, independent of the 4-channel view. On-hand, in-transit and
      PO outflow are real per-warehouse figures; Sales Exp and Target use a flat warehouse-split
      ratio (Bangalore 25% / Gurgaon 23% / Hyderabad 23% / Mumbai 20% / Kolkata 9%), since the daily
      forecast has no warehouse-level granularity.
    </p>

    <div v-for="section in activeSections" :key="section.name" style="margin-bottom: 22px;">
      <p class="scope-title">
        {{ section.name }} — Total required dispatch:
        <span v-for="(d, i) in DOI_TARGETS" :key="d" class="mono">
          {{ fmt(section.requiredByDoi[d]) }} ({{ d }} DOI)<template v-if="i < DOI_TARGETS.length - 1"> / </template>
        </span>
      </p>
      <p class="field-hint" style="margin: 0 0 4px; font-style: italic;">
        SKU wise inventory on hand on {{ section.name }}:
        <span v-for="(sku, i) in SKUS" :key="sku" class="mono">{{ sku }}={{ fmt(section.cells[i] ? section.cells[i].on_hand : 0) }}<template v-if="i < SKUS.length - 1">&nbsp;</template></span>
      </p>
      <p v-if="section.name === 'UC App + PLS'" class="field-hint" style="margin: 0 0 10px; font-style: italic;">
        † ⚠ Target / Req. Dispatch / Status below are the SUM of each warehouse's own independently
        computed gap &mdash; a surplus in one UC warehouse can't offset a deficit in another without an
        actual transfer. Status names the warehouse(s) driving it, so it can read ALREADY SHORT even
        when this row's pooled Proj. Closing looks healthy.
      </p>
      <p v-else-if="isPinned(activeView) && activeScope === 'CHANNEL'" class="field-hint" style="margin: 0 0 10px; font-style: italic;">
        📌 Target Closing here is a live-read committed number for this date (Diwali Sales Plan tab's
        Opening Ask row), re-checked every run &mdash; not hardcoded.
      </p>
      <div v-else style="margin-bottom: 10px;"></div>

      <div class="table-card"><div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>SKU</th>
              <th class="num">{{ section.includesInTransit ? "On-Hand + In Transit" : "On-Hand" }}</th>
              <th class="num">{{ section.includesInTransit ? "PO Outflow" : "PO Inflow" }}</th>
              <th class="num">{{ activeScope === "WAREHOUSE" ? "DRR/day" : "Plan/day" }}</th>
              <th class="num">Sales Exp ({{ windowDays }}d)</th>
              <th class="num">Proj. Closing<br><span class="th-sub">{{ dateLabel(targetDateFor(activeView)) }}</span></th>
              <th class="num">Proj. DOI</th>
              <th class="num">Target ({{ activeDoi }} DOI){{ section.name === "UC App + PLS" ? " †" : "" }}</th>
              <th class="num">Req. Dispatch ({{ activeDoi }} DOI){{ section.name === "UC App + PLS" ? " †" : "" }}</th>
              <th>Status ({{ activeDoi }} DOI){{ section.name === "UC App + PLS" ? " †" : "" }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(c, i) in section.cells" :key="i" :class="c ? STATUS_ROW_CLASS[c.status] : ''">
              <td><b>{{ SKUS[i] }}</b></td>
              <template v-if="c">
                <td class="num mono">{{ fmt(c.on_hand) }}</td>
                <td class="num mono">{{ fmt(c.po_out) }}</td>
                <td class="num mono">{{ fmtRate(windowDays ? (c.sales_expected || 0) / windowDays : null) }}</td>
                <td class="num mono">{{ fmt(c.sales_expected) }}</td>
                <td class="num mono">{{ fmt(c.projected_closing) }}</td>
                <td class="num mono">{{ fmtDoi(c) }}</td>
                <td class="num mono">{{ fmt(c.target_closing) }}</td>
                <td class="num mono"><b>{{ fmt(c.required_dispatch) }}</b></td>
                <td><span class="chip" :class="STATUS_CHIP_CLASS[c.status]">{{ statusLabel(c) }}</span></td>
              </template>
              <template v-else><td colspan="9">&#8211;</td></template>
            </tr>
            <tr class="row-total">
              <td>Total</td>
              <td class="num mono">{{ fmt(section.total.on_hand) }}</td>
              <td class="num mono">{{ fmt(section.total.po_out) }}</td>
              <td class="num mono">{{ fmtRate(windowDays ? section.total.sales_expected / windowDays : null) }}</td>
              <td class="num mono">{{ fmt(section.total.sales_expected) }}</td>
              <td class="num mono">{{ fmt(section.total.projected_closing) }}</td>
              <td class="num mono">&#8211;</td>
              <td class="num mono">{{ fmt(section.total.target_closing) }}</td>
              <td class="num mono">{{ fmt(section.total.required_dispatch) }}</td>
              <td><span class="chip" :class="STATUS_CHIP_CLASS[section.total.status]">{{ section.total.status }}</span></td>
            </tr>
          </tbody>
        </table>
      </div></div>
    </div>

    <h3 class="section-title">Production check ({{ activeDoi }} DOI)</h3>
    <p class="field-hint" style="margin: 0 0 10px;">
      Available supply = UC on-hand + in-transit (network-wide) + production planned in the window;
      Gap = Total Available − Req. Dispatch.
    </p>
    <p v-if="productionCheckIsLegacy" class="field-hint" style="margin: 0 0 10px;">
      Showing the last run's figures, which predate per-DOI-target tracking &mdash; they won't change
      with the DOI toggle until the next dispatch-plan sync.
    </p>
    <div class="table-card"><div class="table-scroll">
      <table>
        <thead><tr>
          <th>SKU</th><th class="num">On-Hand + In-Transit (UC)</th><th class="num">Production Planned</th>
          <th class="num">Total Available</th><th class="num">Req. Dispatch (4-chan)</th><th class="num">Gap</th><th>Status</th>
        </tr></thead>
        <tbody>
          <tr v-for="r in productionCheckForView" :key="r.sku" :class="STATUS_ROW_CLASS[r.status] || ''">
            <td><b>{{ r.sku }}</b></td>
            <td class="num mono">{{ fmt(r.on_hand_in_transit) }}</td>
            <td class="num mono">{{ fmt(r.production_planned) }}</td>
            <td class="num mono">{{ fmt(r.total_available) }}</td>
            <td class="num mono">{{ fmt(r.required) }}</td>
            <td class="num mono">{{ fmt(r.gap) }}</td>
            <td><span class="chip" :class="STATUS_CHIP_CLASS[r.status]">{{ r.status }}</span></td>
          </tr>
          <tr v-if="productionCheckTotal" class="row-total">
            <td>Total</td>
            <td class="num mono">{{ fmt(productionCheckTotal.on_hand_in_transit) }}</td>
            <td class="num mono">{{ fmt(productionCheckTotal.production_planned) }}</td>
            <td class="num mono">{{ fmt(productionCheckTotal.total_available) }}</td>
            <td class="num mono">{{ fmt(productionCheckTotal.required) }}</td>
            <td class="num mono">{{ fmt(productionCheckTotal.gap) }}</td>
            <td><span class="chip" :class="STATUS_CHIP_CLASS[productionCheckTotal.status]">{{ productionCheckTotal.status }}</span></td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
