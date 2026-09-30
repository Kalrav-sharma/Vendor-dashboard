<script setup>
// Last Mile Tracking > Worst Lanes -- (pincode x LSP x facility) cells
// clearing a minimum volume. Selection (which lanes make this list at all,
// and the cap at scripts/sync_last_mile_hourly.py's WORST_LANES_LIMIT)
// stays worst-first server-side -- that's what keeps this "the lanes to
// raise first" rather than every lane in the network. DISPLAY order is
// separate: sorted highest-to-lowest on-time% here per user decision
// 2026-09-29, purely cosmetic and does not change which lanes are included.
import { computed } from "vue";
import { useLastMileData } from "../../composables/useLastMileData.js";
import SummaryKpis from "../SummaryKpis.vue";

const { run, worstLanes, loadError } = useLastMileData();

const sortedLanes = computed(() => [...worstLanes.value].sort(
  (a, b) => (b.on_time_pct ?? -1) - (a.on_time_pct ?? -1)
));

function fmtPct(n) {
  return n == null ? "–" : `${(Math.round(n * 10) / 10).toLocaleString("en-IN")}%`;
}
function fmtDays(n) {
  return n == null ? "–" : `${Math.round(n * 10) / 10}d`;
}
function fmtTatDays(n) {
  return n == null ? "–" : `${n}d`;
}
function pctClass(n) {
  if (n == null) return "";
  if (n < 70) return "cell-critical";
  if (n >= 90) return "cell-good";
  return "";
}

const kpiTiles = computed(() => {
  const lanes = worstLanes.value;
  const critical = lanes.filter(l => (l.on_time_pct ?? 100) < 70).length;
  const gradedTotal = lanes.reduce((s, l) => s + (l.graded || 0), 0);
  const onTimeTotal = lanes.reduce((s, l) => s + Math.max(0, (l.graded || 0) - (l.late || 0)), 0);
  const avgOnTimePct = gradedTotal ? Math.round((onTimeTotal / gradedTotal) * 1000) / 10 : null;
  return [
    // run.total_lanes_active, NOT lanes.length -- user decision 2026-09-29:
    // this table only ever holds the curated top WORST_LANES_LIMIT (25)
    // lanes that also clear the minimum graded volume, so lanes.length was
    // "how many made that curated list", never the real count of (LSP,
    // city) lanes actually in use across every carrier.
    { label: "Total lanes", value: run.value?.total_lanes_active ?? "–" },
    { label: "Below 70% on-time", value: critical, cls: critical > 0 ? "critical" : "" },
    { label: "Average on-time % (lanes shown)", value: avgOnTimePct == null ? "–" : `${avgOnTimePct}%`,
      cls: avgOnTimePct != null && avgOnTimePct < 80 ? "critical" : "good" },
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
    <div class="table-card"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>LSP</th><th>City</th>
            <th class="num">Graded</th><th class="num">Late</th>
            <th class="num">On-time %</th>
            <th class="num">Promised TAT</th>
            <th class="num">Avg transit</th><th class="num">Suggested TAT</th>
            <th class="num">Active</th><th class="num">Breached</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!sortedLanes.length">
            <td colspan="10" class="empty-state">No lanes cleared the minimum graded volume for this run.</td>
          </tr>
          <tr v-for="l in sortedLanes" :key="l.id">
            <td><b>{{ l.lsp }}</b></td>
            <td>{{ l.city || "–" }}</td>
            <td class="num mono">{{ l.graded }}</td>
            <td class="num mono">{{ l.late }}</td>
            <td class="num mono" :class="pctClass(l.on_time_pct)">{{ fmtPct(l.on_time_pct) }}</td>
            <td class="num mono">{{ fmtTatDays(l.promised_tat_days) }}</td>
            <td class="num mono">{{ fmtDays(l.avg_transit_days) }}</td>
            <td class="num mono" :class="l.p85_transit_days != null && l.promised_tat_days != null && l.p85_transit_days > l.promised_tat_days ? 'cell-critical' : ''">{{ fmtDays(l.p85_transit_days) }}</td>
            <td class="num mono">{{ l.active }}</td>
            <td class="num mono" :class="l.breached > 0 ? 'cell-critical' : ''">{{ l.breached }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
