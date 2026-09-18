<script setup>
// Last Mile Tracking > Carrier Performance -- one row per LSP over the
// run's trailing window, delivered/on-time/late + on-time%. courier_codes
// is shown collapsed (count only) since a single LSP can carry many
// Uniware courier codes (e.g. Shadowfax has one per city).
import { computed } from "vue";
import { useLastMileData } from "../../composables/useLastMileData.js";
import SummaryKpis from "../SummaryKpis.vue";

const { run, lspPerf, loadError } = useLastMileData();

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
  const delivered = lspPerf.value.reduce((s, r) => s + (r.delivered || 0), 0);
  const onTime = lspPerf.value.reduce((s, r) => s + (r.on_time || 0), 0);
  const overallPct = delivered ? Math.round((onTime / delivered) * 1000) / 10 : null;
  const worst = [...lspPerf.value].sort((a, b) => (a.on_time_pct ?? 100) - (b.on_time_pct ?? 100))[0];
  return [
    { label: "LSPs tracked", value: lspPerf.value.length },
    { label: "Delivered (window)", value: delivered.toLocaleString("en-IN") },
    { label: "Overall on-time %", value: fmtPct(overallPct), cls: overallPct != null && overallPct < 80 ? "critical" : "good" },
    { label: "Worst LSP", value: worst ? `${worst.lsp} (${fmtPct(worst.on_time_pct)})` : "–" },
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
      Run {{ run.run_id }} · trailing {{ run.window_days }} days
    </p>

    <div class="table-card"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>LSP</th><th class="num">Courier codes</th>
            <th class="num">Delivered</th><th class="num">On time</th><th class="num">Late</th>
            <th class="num">On-time %</th><th class="num">Excluded</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!lspPerf.length">
            <td colspan="7" class="empty-state">No carrier performance data for this run.</td>
          </tr>
          <tr v-for="r in lspPerf" :key="r.id">
            <td><b>{{ r.lsp }}</b></td>
            <td class="num mono">{{ (r.courier_codes || []).length }}</td>
            <td class="num mono">{{ r.delivered }}</td>
            <td class="num mono">{{ r.on_time }}</td>
            <td class="num mono">{{ r.late }}</td>
            <td class="num mono" :class="pctClass(r.on_time_pct)">{{ fmtPct(r.on_time_pct) }}</td>
            <td class="num mono">{{ r.excluded || 0 }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
