<script setup>
// Last Mile Tracking > Carrier Performance -- one meter per LSP over the
// run's trailing window. Infographic by user decision 2026-09-29 (was a
// table); a "View as table" toggle keeps the WCAG-clean table equivalent
// one click away rather than removing it.
//
// Bar/fill color is deliberately a single neutral accent, not a
// good/warning/critical hue per bar: this app's own status colors
// (--good/--open/--critical) are audited for TEXT/chip use, not as
// adjacent painted marks -- validated against the dataviz skill's CVD
// checks 2026-09-29, the --open (amber) tier fails the normal-vision
// floor against both --good and --critical, and even the clean --good/
// --critical pair fails lightness-band and CVD separation in dark mode
// specifically (those hex values are pastel-tuned for text-on-dark-
// surface contrast, not for use as filled areas). Severity instead rides
// the on-time% figure's own text color (the same cell-good/cell-critical
// convention already used in every other Last Mile table), so identity
// is never color-alone on the meter itself.
import { computed, ref } from "vue";
import { useLastMileData } from "../../composables/useLastMileData.js";
import SummaryKpis from "../SummaryKpis.vue";

const { run, lspPerf, loadError } = useLastMileData();
const showTable = ref(false);

function fmtPct(n) {
  return n == null ? "–" : `${(Math.round(n * 10) / 10).toLocaleString("en-IN")}%`;
}
function fmtClass(n) {
  if (n == null) return "";
  if (n < 70) return "critical";
  if (n >= 90) return "good";
  return "";
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
    <div style="display: flex; justify-content: flex-end; margin-bottom: 10px;">
      <button class="link-btn" style="font-size: 0.8rem;" @click="showTable = !showTable">
        {{ showTable ? "View as chart" : "View as table" }}
      </button>
    </div>

    <div v-if="!lspPerf.length" class="empty-state" style="padding: 40px 0;">
      No carrier performance data for this run.
    </div>

    <!-- Infographic: one meter per LSP, biggest carrier first (server-sorted). -->
    <div v-else-if="!showTable" class="lsp-meter-list">
      <div v-for="r in lspPerf" :key="r.id" class="lsp-meter-row">
        <div class="lsp-meter-head">
          <div>
            <b>{{ r.lsp }}</b>
            <span class="muted-text" style="margin-left: 8px;">{{ r.delivered }} delivered</span>
          </div>
          <span class="mono" :class="fmtClass(r.on_time_pct)" style="font-size: 1.05rem; font-weight: 600;">
            {{ fmtPct(r.on_time_pct) }}
          </span>
        </div>
        <div class="meter-track">
          <div class="meter-fill" :style="{ width: (r.on_time_pct ?? 0) + '%' }"></div>
        </div>
        <div class="lsp-meter-stats">
          <span>On time <b class="mono">{{ r.on_time }}</b></span>
          <span>Late <b class="mono">{{ r.late }}</b></span>
          <span title="Delivered shipments with no matching SERVICEABILITYRULES_DP rule for this lane -- graded neither on-time nor late, because there is no real promise to grade against (falls back to an assumed default). Not a data-quality error, and not counted in the on-time % above.">
            Excluded <b class="mono">{{ r.excluded || 0 }}</b> <span class="muted-text">(assumed promise)</span>
          </span>
          <span class="muted-text">{{ (r.courier_codes || []).length }} courier code(s)</span>
        </div>
      </div>
    </div>

    <!-- Table view: the WCAG-clean equivalent, one click away. -->
    <div v-else class="table-card"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>LSP</th><th class="num">Courier codes</th>
            <th class="num">Delivered</th><th class="num">On time</th><th class="num">Late</th>
            <th class="num">On-time %</th><th class="num">Excluded</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in lspPerf" :key="r.id">
            <td><b>{{ r.lsp }}</b></td>
            <td class="num mono">{{ (r.courier_codes || []).length }}</td>
            <td class="num mono">{{ r.delivered }}</td>
            <td class="num mono">{{ r.on_time }}</td>
            <td class="num mono">{{ r.late }}</td>
            <td class="num mono" :class="pctClass(r.on_time_pct)">{{ fmtPct(r.on_time_pct) }}</td>
            <td class="num mono" title="Delivered shipments with no matching SERVICEABILITYRULES_DP rule for this lane -- graded neither on-time nor late (falls back to an assumed default), and not counted in on-time %.">{{ r.excluded || 0 }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
