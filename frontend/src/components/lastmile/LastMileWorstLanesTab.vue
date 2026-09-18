<script setup>
// Last Mile Tracking > Worst Lanes -- (pincode x LSP x facility) cells
// clearing a minimum volume, worst on-time% first. Where Operations
// should look first, not every lane in the network.
import { computed } from "vue";
import { useLastMileData } from "../../composables/useLastMileData.js";
import SummaryKpis from "../SummaryKpis.vue";

const { run, worstLanes, loadError } = useLastMileData();

function fmtPct(n) {
  return n == null ? "–" : `${(Math.round(n * 10) / 10).toLocaleString("en-IN")}%`;
}
function fmtDays(n) {
  return n == null ? "–" : `${Math.round(n * 10) / 10}d`;
}
function pctClass(n) {
  if (n == null) return "";
  if (n < 70) return "cell-critical";
  if (n >= 90) return "cell-good";
  return "";
}

const kpiTiles = computed(() => {
  const critical = worstLanes.value.filter(l => (l.on_time_pct ?? 100) < 70).length;
  return [
    { label: "Lanes flagged", value: worstLanes.value.length },
    { label: "Below 70% on-time", value: critical, cls: critical > 0 ? "critical" : "" },
  ];
});
</script>

<template>
  <!-- Only render KPI tiles once a run exists. With no run the counts are all
       zero, and a tile reading "0" is indistinguishable from a real measurement
       of zero -- it must not look like we checked and found nothing wrong. -->
  <SummaryKpis v-if="run" :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>
  <div v-else-if="!run" class="empty-state" style="padding: 40px 0;">
    No sync has run yet, so there is nothing to show -- these are not measured zeros.
    The rollup tables are populated by the hourly courier-tracking job, which is not built yet;
    scripts/sync_last_mile_daily.py currently only builds the shipment watchlist.
  </div>

  <template v-else>
    <p class="field-hint" style="margin: 0 0 12px;">
      Run {{ run.run_id }} · trailing {{ run.window_days }} days · lanes below the sync's minimum graded volume are not shown. "Graded" counts only shipments with a real promise date that have actually resolved on-time or late -- in-flight and assumed-promise shipments cannot be graded.
    </p>

    <div class="table-card"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>LSP</th><th>City</th>
            <th class="num">Graded</th><th class="num">Late</th>
            <th class="num">On-time %</th>
            <th class="num">Avg transit</th><th class="num">P85 transit</th>
            <th class="num">Active</th><th class="num">Breached</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!worstLanes.length">
            <td colspan="9" class="empty-state">No lanes cleared the minimum graded volume for this run.</td>
          </tr>
          <tr v-for="l in worstLanes" :key="l.id">
            <td><b>{{ l.lsp }}</b></td>
            <td>{{ l.city || "–" }}</td>
            <td class="num mono">{{ l.graded }}</td>
            <td class="num mono">{{ l.late }}</td>
            <td class="num mono" :class="pctClass(l.on_time_pct)">{{ fmtPct(l.on_time_pct) }}</td>
            <td class="num mono">{{ fmtDays(l.avg_transit_days) }}</td>
            <td class="num mono">{{ fmtDays(l.p85_transit_days) }}</td>
            <td class="num mono">{{ l.active }}</td>
            <td class="num mono" :class="l.breached > 0 ? 'cell-critical' : ''">{{ l.breached }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
