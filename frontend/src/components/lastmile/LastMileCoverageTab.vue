<script setup>
// Last Mile Tracking > Coverage & Data Quality -- "is this dashboard
// seeing everything it should" (coverage) and "is the source data clean
// enough to trust" (data quality). Both are about the PIPELINE, not
// delivery performance -- kept on one tab since neither is something
// Operations acts on day to day, unlike Alerts/Carrier/Lanes.
import { computed } from "vue";
import { useLastMileData } from "../../composables/useLastMileData.js";
import SummaryKpis from "../SummaryKpis.vue";

const { run, coverage, dqSummary, loadError } = useLastMileData();

function fmt(n) {
  return n == null ? "–" : n.toLocaleString("en-IN");
}
function entries(obj) {
  return obj ? Object.entries(obj).sort((a, b) => b[1] - a[1]) : [];
}

const kpiTiles = computed(() => {
  if (!coverage.value) return [];
  const c = coverage.value;
  const coveredPct = c.shipments_total
    ? Math.round((c.carrier_assigned / c.shipments_total) * 1000) / 10 : null;
  return [
    { label: "Shipments (window)", value: fmt(c.shipments_total) },
    { label: "Open", value: fmt(c.open_total) },
    { label: "Carrier-assigned", value: fmt(c.carrier_assigned) },
    { label: "Coverage %", value: coveredPct != null ? `${coveredPct}%` : "–", cls: coveredPct != null && coveredPct < 90 ? "critical" : "good" },
  ];
});
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>
  <div v-else-if="!run" class="empty-state" style="padding: 40px 0;">
    No sync has run yet -- Last Mile Tracking has no data on file. See scripts/sync_last_mile.py.
  </div>

  <template v-else>
    <p class="field-hint" style="margin: 0 0 12px;">Run {{ run.run_id }} · trailing {{ run.window_days }} days</p>

    <h3 class="section-title">Excluded from tracking</h3>
    <div class="panel-grid" style="margin-bottom: 28px;">
      <div class="panel">
        <div class="field-hint" style="margin-bottom: 6px;">By adapter</div>
        <div v-if="!entries(coverage?.excluded_by_adapter).length" class="cell-empty">None</div>
        <div v-for="[k, v] in entries(coverage?.excluded_by_adapter)" :key="k">{{ k }}: <b>{{ fmt(v) }}</b></div>
      </div>
      <div class="panel">
        <div class="field-hint" style="margin-bottom: 6px;">By reason</div>
        <div v-if="!entries(coverage?.excluded_reasons).length" class="cell-empty">None</div>
        <div v-for="[k, v] in entries(coverage?.excluded_reasons)" :key="k">{{ k }}: <b>{{ fmt(v) }}</b></div>
      </div>
      <div class="panel">
        <div class="field-hint" style="margin-bottom: 6px;">Other</div>
        <div>Not trackable by design: <b>{{ fmt(coverage?.not_trackable_by_design) }}</b></div>
        <div>No adapter rule: <b>{{ fmt(coverage?.no_adapter_rule) }}</b></div>
        <div>Excluded by scope: <b>{{ fmt(coverage?.excluded_by_scope) }}</b></div>
      </div>
    </div>

    <h3 class="section-title">Data quality</h3>
    <p class="field-hint" style="margin: 0 0 10px;">
      Upstream problems in the source data -- a high number here means the numbers on the other tabs are not yet trustworthy, not that the couriers are doing badly.
    </p>
    <div class="table-card"><div class="table-scroll">
      <table>
        <thead><tr><th>Issue</th><th class="num">Distinct values</th><th class="num">Occurrences</th></tr></thead>
        <tbody v-if="dqSummary">
          <tr>
            <td>Unmapped status</td>
            <td class="num mono">{{ fmt(dqSummary.unmapped_status_distinct) }}</td>
            <td class="num mono" :class="dqSummary.unmapped_status_occurrences > 0 ? 'cell-critical' : ''">{{ fmt(dqSummary.unmapped_status_occurrences) }}</td>
          </tr>
          <tr>
            <td>Unmapped courier</td>
            <td class="num mono">{{ fmt(dqSummary.unmapped_courier_distinct) }}</td>
            <td class="num mono" :class="dqSummary.unmapped_courier_occurrences > 0 ? 'cell-critical' : ''">{{ fmt(dqSummary.unmapped_courier_occurrences) }}</td>
          </tr>
          <tr>
            <td>Malformed AWB pattern</td>
            <td class="num mono">{{ fmt(dqSummary.awb_pattern_distinct) }}</td>
            <td class="num mono" :class="dqSummary.awb_pattern_occurrences > 0 ? 'cell-critical' : ''">{{ fmt(dqSummary.awb_pattern_occurrences) }}</td>
          </tr>
          <tr>
            <td>Missing AWB</td>
            <td class="num mono">{{ fmt(dqSummary.missing_awb_distinct) }}</td>
            <td class="num mono" :class="dqSummary.missing_awb_occurrences > 0 ? 'cell-critical' : ''">{{ fmt(dqSummary.missing_awb_occurrences) }}</td>
          </tr>
        </tbody>
        <tbody v-else>
          <tr><td colspan="3" class="empty-state">No data quality summary for this run.</td></tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
