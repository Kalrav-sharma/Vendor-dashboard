<script setup>
// Last Mile Tracking > Open Shipments -- the full "not complete, not RTO"
// entry point: every AWB in cohort (live, backlog, no_dispatch_date),
// whether or not it currently trips an alert. Alerts is a deliberately
// CURATED subset (only shipments alerts.evaluate() flags); this is the
// superset it's drawn from -- a shipment moving fine, not yet overdue,
// belongs here even though it never becomes an alert. That's the real
// answer to "what does this pipeline still need to track to completion",
// which a filtered alerts-only view can't show on its own.
import { computed, reactive } from "vue";
import { useLastMileData } from "../../composables/useLastMileData.js";
import SummaryKpis from "../SummaryKpis.vue";

const { run, openShipments, loadError } = useLastMileData();

const filters = reactive({ search: "", cohort: "", lsp: "", city: "", onlyAlerted: false });

const COHORT_LABEL = { live: "Live", backlog: "Backlog", no_dispatch_date: "No dispatch date" };
const BUCKET_CLASS = { rescue: "critical", closed_failure: "muted", data_quality: "open" };

const cohortOptions = computed(() => [...new Set(openShipments.value.map(s => s.cohort))].sort());
const lspOptions = computed(() => [...new Set(openShipments.value.map(s => s.lsp).filter(Boolean))].sort());
const cityOptions = computed(() => [...new Set(openShipments.value.map(s => s.city).filter(Boolean))].sort());

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
function fmtPayment(p) {
  if (p === "COD" || p === "Prepaid") return p;
  return "–";
}
// UNKNOWN (status_source="none") is a real, deliberate answer -- "we don't
// have a live poll for this shipment right now" -- not a placeholder for
// missing data. See sync_last_mile_hourly.py's alerts.fuse() for why.
function fmtStatus(s) {
  return s.status === "UNKNOWN" ? "Not yet polled" : (s.status || s.raw_status || "–");
}

const filteredSorted = computed(() => openShipments.value.filter(s => {
  if (filters.onlyAlerted && !s.has_alert) return false;
  if (filters.cohort && s.cohort !== filters.cohort) return false;
  if (filters.lsp && s.lsp !== filters.lsp) return false;
  if (filters.city && s.city !== filters.city) return false;
  if (filters.search) {
    const q = filters.search.toLowerCase();
    const hay = [s.awb, s.city, s.pincode, s.lsp, ...(s.sale_order_codes || [])].join(" ").toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}));

const kpiTiles = computed(() => {
  const all = openShipments.value;
  const alerted = all.filter(s => s.has_alert).length;
  const overdue = all.filter(s => s.days_overdue > 0).length;
  const unpolled = all.filter(s => s.status === "UNKNOWN").length;
  return [
    { label: "Total open shipments", value: all.length },
    { label: "Currently alerted", value: alerted, cls: alerted > 0 ? "critical" : "good" },
    { label: "Overdue", value: overdue, cls: overdue > 0 ? "critical" : "" },
    { label: "Not yet polled this run", value: unpolled },
  ];
});

// CSV of exactly what's currently filtered/visible.
const CSV_COLUMNS = [
  ["awb", "AWB"], ["cohort", "Cohort"], ["lsp", "LSP"], ["city", "City"], ["pincode", "Pincode"],
  ["payment_type", "Payment"], ["order", "Order"], ["status", "Status"], ["promised_date", "Promised"],
  ["days_overdue", "Overdue (days)"], ["has_alert", "Alerted"], ["primary_flag", "Flag"],
];
function csvCell(v) {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function downloadCsv() {
  const rows = filteredSorted.value.map(s => ({
    awb: s.awb, cohort: COHORT_LABEL[s.cohort] || s.cohort, lsp: s.lsp || "", city: s.city || "",
    pincode: s.pincode || "", payment_type: fmtPayment(s.payment_type),
    order: (s.sale_order_codes || [])[0] || "", status: fmtStatus(s), promised_date: fmtDate(s.promised_date),
    days_overdue: s.days_overdue ?? "", has_alert: s.has_alert ? "Yes" : "No",
    primary_flag: s.primary_flag ? fmtFlag(s.primary_flag) : "",
  }));
  const lines = [
    CSV_COLUMNS.map(([, label]) => csvCell(label)).join(","),
    ...rows.map(r => CSV_COLUMNS.map(([key]) => csvCell(r[key])).join(",")),
  ];
  const blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const stamp = new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-");
  a.href = url;
  a.download = `last-mile-open-shipments-${stamp}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
</script>

<template>
  <SummaryKpis v-if="run" :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>
  <div v-else-if="!run" class="empty-state" style="padding: 40px 0;">
    No sync has run yet, so there is nothing to show -- these are not measured zeros.
  </div>

  <template v-else>
    <p class="field-hint" style="margin: 0 0 12px;">
      Run {{ run.run_id }} · every shipment not yet delivered, cancelled, returned or excluded --
      the full population Alerts is drawn from, not just the ones currently flagged.
    </p>

    <div style="display: flex; align-items: flex-end; gap: 16px; margin-bottom: 14px; flex-wrap: wrap;">
      <div class="field" style="max-width: 340px; margin-bottom: 0;">
        <label for="open-search">Search AWB, order, city, pincode…</label>
        <input id="open-search" v-model="filters.search" type="text" placeholder="Type to search…">
      </div>
      <label style="display: flex; align-items: center; gap: 6px; font-size: 0.85rem; padding-bottom: 9px;">
        <input type="checkbox" v-model="filters.onlyAlerted"> Only currently alerted
      </label>
      <button
        class="primary-btn" style="width: auto; padding: 9px 16px;"
        :disabled="!filteredSorted.length" @click="downloadCsv"
      >Download as CSV ({{ filteredSorted.length }})</button>
    </div>

    <div class="table-card"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>AWB</th><th>Cohort</th><th>LSP</th><th>City / pincode</th><th>Payment</th>
            <th>Order</th><th>Status</th><th>Promised</th><th class="num">Overdue</th><th>Alert</th>
          </tr>
          <tr class="filter-row">
            <td></td>
            <td>
              <select v-model="filters.cohort">
                <option value="">All</option>
                <option v-for="c in cohortOptions" :key="c" :value="c">{{ COHORT_LABEL[c] || c }}</option>
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
            <td></td><td></td><td></td><td></td><td></td><td></td>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!filteredSorted.length">
            <td colspan="10" class="empty-state">No open shipments match these filters.</td>
          </tr>
          <tr v-for="s in filteredSorted" :key="s.id">
            <td class="mono">{{ s.awb }}</td>
            <td>{{ COHORT_LABEL[s.cohort] || s.cohort }}</td>
            <td>{{ s.lsp || "–" }}</td>
            <td>{{ s.city || "–" }}<span v-if="s.pincode" class="mono" style="color: var(--muted);"> · {{ s.pincode }}</span></td>
            <td><span v-if="fmtPayment(s.payment_type) !== '–'" class="chip" :class="s.payment_type === 'COD' ? 'chip-open' : 'chip-muted'">{{ fmtPayment(s.payment_type) }}</span><span v-else>–</span></td>
            <td class="mono">{{ (s.sale_order_codes || [])[0] || "–" }}</td>
            <td :class="s.status === 'UNKNOWN' ? 'cell-empty' : ''">{{ fmtStatus(s) }}</td>
            <td>{{ fmtDate(s.promised_date) }}</td>
            <td class="num mono" :class="s.days_overdue > 0 ? 'cell-critical' : ''">{{ fmtDays(s.days_overdue) }}</td>
            <td>
              <span v-if="s.has_alert" class="chip" :class="`chip-${BUCKET_CLASS[s.bucket] || 'critical'}`">{{ fmtFlag(s.primary_flag) }}</span>
              <span v-else class="chip chip-good">OK</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
