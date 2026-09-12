<script setup>
// S&OP > PO Fulfillment -- date-wise PO grid (rolling 10-day simulation),
// Action Items (Reschedule/Partial grouped by SKU), and a production-
// shortfall RCA panel. Live replica of /sop-master's delegated
// po-fulfillment-alert dashboard, now backed by a scheduled sync instead
// of a one-shot agent run.
import { computed } from "vue";
import { useSopPoFulfillmentData } from "../../composables/useSopPoFulfillmentData.js";
import { useSopPoFulfillmentFilters } from "../../composables/useSopPoFulfillmentFilters.js";
import SummaryKpis from "../SummaryKpis.vue";

const STATUS_CHIP_CLASS = {
  "CONFIRMED": "chip-open",
  "FULFILL": "chip-good",
  "TRANSIT-FULFILL": "chip-good",
  "PARTIAL/NEEDS IN-TRANSIT": "chip-critical",
  "RESCHEDULE": "chip-critical",
};

const { rows, actionItems, rca, runDate, loadError } = useSopPoFulfillmentData();
const { filters, filteredSorted, warehouseOptions, channelOptions, skuOptions, statusOptions } =
  useSopPoFulfillmentFilters(rows);

function fmt(n) {
  return Math.round(n || 0).toLocaleString("en-IN");
}
function dateLabel(ymd) {
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "UTC" });
}

const kpiTiles = computed(() => {
  const counts = {};
  for (const r of rows.value) counts[r.status] = (counts[r.status] || 0) + 1;
  return [
    { label: "Confirmed", value: fmt(counts["CONFIRMED"]) },
    { label: "Fulfill", value: fmt((counts["FULFILL"] || 0) + (counts["TRANSIT-FULFILL"] || 0)), cls: "good" },
    { label: "Partial", value: fmt(counts["PARTIAL/NEEDS IN-TRANSIT"]), cls: "critical" },
    { label: "Reschedule", value: fmt(counts["RESCHEDULE"]), cls: "critical" },
  ];
});

const rescheduleGroups = computed(() => actionItems.value.filter(a => a.bucket === "RESCHEDULE"));
const partialGroups = computed(() => actionItems.value.filter(a => a.bucket === "PARTIAL"));

const RCA_OUTCOME_LABEL = {
  SHORTFALL_DATES_FOUND: "Shortfall found",
  NO_SHORTFALL_LIKELY_PO_VOLUME: "No shortfall",
  UNAVAILABLE: "Unavailable",
};
const RCA_OUTCOME_CLS = {
  SHORTFALL_DATES_FOUND: "chip-critical",
  NO_SHORTFALL_LIKELY_PO_VOLUME: "chip-good",
  UNAVAILABLE: "chip-muted",
};
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>
  <div v-if="!runDate" class="empty-state">No PO fulfillment run yet -- the sync workflow hasn't populated this table.</div>

  <template v-else>
    <div class="table-card" style="margin-bottom: 24px;">
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Date</th><th>PO Number</th><th>Warehouse</th><th>Channel</th><th>SKU</th>
              <th class="num">PO Qty</th><th>Status & Detail</th>
            </tr>
            <tr class="filter-row">
              <td></td>
              <td><input v-model="filters.search" type="text" placeholder="Search..."></td>
              <td>
                <select v-model="filters.warehouse"><option value="">All</option>
                  <option v-for="w in warehouseOptions" :key="w" :value="w">{{ w }}</option>
                </select>
              </td>
              <td>
                <select v-model="filters.channel"><option value="">All</option>
                  <option v-for="c in channelOptions" :key="c" :value="c">{{ c }}</option>
                </select>
              </td>
              <td>
                <select v-model="filters.sku"><option value="">All</option>
                  <option v-for="s in skuOptions" :key="s" :value="s">{{ s }}</option>
                </select>
              </td>
              <td></td>
              <td>
                <select v-model="filters.status"><option value="">All</option>
                  <option v-for="s in statusOptions" :key="s" :value="s">{{ s }}</option>
                </select>
              </td>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in filteredSorted" :key="i">
              <td>{{ dateLabel(r.sim_date) }}</td>
              <td class="mono">{{ r.po_number || "–" }}</td>
              <td>{{ r.warehouse }}</td>
              <td>{{ r.channel }}</td>
              <td><span class="chip chip-muted">{{ r.sku }}</span></td>
              <td class="num mono">{{ fmt(r.po_qty) }}</td>
              <td>
                <span class="chip" :class="STATUS_CHIP_CLASS[r.status] || 'chip-muted'">{{ r.status }}</span>
                <span style="margin-left: 8px;">{{ r.detail }}</span>
              </td>
            </tr>
            <tr v-if="!filteredSorted.length"><td colspan="7" class="empty-state">No rows match the current filters.</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="panel" style="margin-bottom: 20px;">
      <h2>Action items -- Reschedule</h2>
      <div v-if="!rescheduleGroups.length" class="empty-state">Nothing to reschedule.</div>
      <div v-for="g in rescheduleGroups" :key="g.sku" style="margin-bottom: 14px;">
        <span class="chip chip-critical">{{ g.sku }}</span>
        <b style="margin-left: 8px;">{{ fmt(g.total_qty) }} units</b>
        <p style="color: var(--muted); font-size: 0.83rem; margin-top: 4px;">{{ g.note }}</p>
      </div>
    </div>

    <div class="panel" style="margin-bottom: 20px;">
      <h2>Action items -- Partial</h2>
      <div v-if="!partialGroups.length" class="empty-state">Nothing partially fulfilled.</div>
      <div v-for="g in partialGroups" :key="g.sku" style="margin-bottom: 14px;">
        <span class="chip chip-critical">{{ g.sku }}</span>
        <b style="margin-left: 8px;">{{ fmt(g.total_qty) }} units</b>
        <p style="color: var(--muted); font-size: 0.83rem; margin-top: 4px;">{{ g.note }}</p>
      </div>
    </div>

    <div class="panel">
      <h2>Production-shortfall RCA</h2>
      <div v-if="!rca.length" class="empty-state">No RESCHEDULE/PARTIAL rows this run -- nothing to explain.</div>
      <div v-for="r in rca" :key="r.sku" style="margin-bottom: 12px;">
        <span class="chip chip-muted">{{ r.sku }}</span>
        <span class="chip" :class="RCA_OUTCOME_CLS[r.outcome]" style="margin-left: 8px;">{{ RCA_OUTCOME_LABEL[r.outcome] }}</span>
        <p style="font-size: 0.83rem; margin-top: 4px;">{{ r.note }}</p>
      </div>
    </div>
  </template>
</template>
