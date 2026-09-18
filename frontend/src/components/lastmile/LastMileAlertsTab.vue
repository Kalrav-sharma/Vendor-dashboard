<script setup>
// Last Mile Tracking > Alerts -- the actionable queue. Every other tab is
// context for this one. One row per shipment currently flagged by
// scripts/sync_last_mile.py; bucket groups WHAT KIND of action is needed
// (see that table's comment in schema.sql):
//   rescue         -- still fixable, Operations should chase
//   closed_failure -- resolved but badly (lost, RTO) -- reporting only
//   data_quality   -- can't be judged -- a pipeline gap, not a delivery failure
import { computed, reactive } from "vue";
import { useLastMileData } from "../../composables/useLastMileData.js";
import SummaryKpis from "../SummaryKpis.vue";

const { run, alerts, loadError } = useLastMileData();

const filters = reactive({ search: "", bucket: "", lsp: "", city: "" });

const bucketOptions = computed(() => [...new Set(alerts.value.map(a => a.bucket))].sort());
const lspOptions = computed(() => [...new Set(alerts.value.map(a => a.lsp).filter(Boolean))].sort());
const cityOptions = computed(() => [...new Set(alerts.value.map(a => a.city).filter(Boolean))].sort());

const BUCKET_LABEL = { rescue: "Rescue", closed_failure: "Closed (failure)", data_quality: "Data quality" };
const BUCKET_CLASS = { rescue: "critical", closed_failure: "muted", data_quality: "open" };

function fmtFlag(f) {
  return (f || "").replace(/_/g, " ").toLowerCase().replace(/^./, c => c.toUpperCase());
}
function fmtDate(d) {
  if (!d) return "–";
  return new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}
function fmtDays(n) {
  if (n == null) return "–";
  const r = Math.round(n * 10) / 10;
  return r > 0 ? `+${r}d` : `${r}d`;
}

const filteredSorted = computed(() => alerts.value.filter(a => {
  if (filters.bucket && a.bucket !== filters.bucket) return false;
  if (filters.lsp && a.lsp !== filters.lsp) return false;
  if (filters.city && a.city !== filters.city) return false;
  if (filters.search) {
    const q = filters.search.toLowerCase();
    const hay = [a.awb, a.city, a.pincode, a.lsp, ...(a.sale_order_codes || [])].join(" ").toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}));

const kpiTiles = computed(() => {
  const rescue = alerts.value.filter(a => a.bucket === "rescue").length;
  const closedFailure = alerts.value.filter(a => a.bucket === "closed_failure").length;
  const dataQuality = alerts.value.filter(a => a.bucket === "data_quality").length;
  return [
    { label: "Total alerts", value: alerts.value.length },
    { label: "Rescue -- action needed", value: rescue, cls: rescue > 0 ? "critical" : "" },
    { label: "Closed (failure)", value: closedFailure, cls: closedFailure > 0 ? "" : "" },
    { label: "Data quality gaps", value: dataQuality, cls: dataQuality > 0 ? "" : "" },
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
      Run {{ run.run_id }} · generated {{ new Date(run.generated_at).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) }}
      · trailing {{ run.window_days }} days
    </p>

    <div class="field" style="max-width: 340px; margin-bottom: 14px;">
      <label for="lastmile-search">Search AWB, order, city, pincode…</label>
      <input id="lastmile-search" v-model="filters.search" type="text" placeholder="Type to search…">
    </div>

    <div class="table-card"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>AWB</th><th>Flag</th><th>Bucket</th><th>LSP</th><th>City / pincode</th>
            <th>Order</th><th>Status</th><th>Promised</th><th class="num">Overdue</th><th>Notes</th>
          </tr>
          <tr class="filter-row">
            <td></td><td></td>
            <td>
              <select v-model="filters.bucket">
                <option value="">All</option>
                <option v-for="b in bucketOptions" :key="b" :value="b">{{ BUCKET_LABEL[b] || b }}</option>
              </select>
            </td>
            <td>
              <select v-model="filters.lsp">
                <option value="">All</option>
                <option v-for="l in lspOptions" :key="l" :value="l">{{ l }}</option>
              </select>
            </td>
            <td>
              <select v-model="filters.city">
                <option value="">All</option>
                <option v-for="c in cityOptions" :key="c" :value="c">{{ c }}</option>
              </select>
            </td>
            <td></td><td></td><td></td><td></td><td></td>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!filteredSorted.length">
            <td colspan="10" class="empty-state">No alerts match these filters.</td>
          </tr>
          <tr v-for="a in filteredSorted" :key="a.id">
            <td class="mono">{{ a.awb }}</td>
            <td><span class="chip chip-critical">{{ fmtFlag(a.primary_flag) }}</span></td>
            <td><span class="chip" :class="`chip-${BUCKET_CLASS[a.bucket]}`">{{ BUCKET_LABEL[a.bucket] || a.bucket }}</span></td>
            <td>{{ a.lsp || "–" }}</td>
            <td>{{ a.city || "–" }}<span v-if="a.pincode" class="mono" style="color: var(--muted);"> · {{ a.pincode }}</span></td>
            <td class="mono">{{ (a.sale_order_codes || [])[0] || "–" }}</td>
            <td>{{ a.status || a.raw_status || "–" }}</td>
            <td>{{ fmtDate(a.promised_date) }}</td>
            <td class="num mono" :class="a.days_overdue > 0 ? 'cell-critical' : ''">{{ fmtDays(a.days_overdue) }}</td>
            <td>{{ a.ndr_reason || a.notes || "–" }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
