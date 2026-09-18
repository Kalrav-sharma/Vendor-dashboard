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
      Run {{ run.run_id }} · trailing {{ run.window_days }} days · lanes below the sync's minimum volume threshold are not shown
    </p>

    <div class="table-card"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Pincode</th><th>City</th><th>Facility</th><th>LSP</th>
            <th class="num">Volume</th><th class="num">Delivered</th>
            <th class="num">On-time %</th><th class="num">Avg days late</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!worstLanes.length">
            <td colspan="8" class="empty-state">No lanes cleared the minimum volume threshold for this run.</td>
          </tr>
          <tr v-for="l in worstLanes" :key="l.id">
            <td class="mono">{{ l.pincode || "–" }}</td>
            <td>{{ l.city || "–" }}</td>
            <td class="mono">{{ l.facility_code || "–" }}</td>
            <td><b>{{ l.lsp }}</b></td>
            <td class="num mono">{{ l.volume }}</td>
            <td class="num mono">{{ l.delivered }}</td>
            <td class="num mono" :class="pctClass(l.on_time_pct)">{{ fmtPct(l.on_time_pct) }}</td>
            <td class="num mono">{{ l.avg_days_late != null ? (Math.round(l.avg_days_late * 10) / 10) : "–" }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
