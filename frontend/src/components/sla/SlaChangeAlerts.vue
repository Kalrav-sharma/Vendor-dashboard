<script setup>
// SLA › SLA change alerts: pincodes of the RCA week whose SLA (promised TAT) looks set wrong.
// Rule (user, 2026-09-29), computed by sync_sla_portal.js step 1c: a pincode with a delivered RO
// order in the RCA week, >= 5 delivered orders over the RCA week + 4 previous promised weeks,
// and < 88% of them on time. "SLA revised" is the user's own per-week tracking toggle.
import { ref, computed } from "vue";
import "../health/health.css";
import { useSlaPincodeAlerts } from "../../composables/useSlaPincodeAlerts.js";
import { otdCls, downloadCsv } from "./slaUtil.js";

const props = defineProps({
  run: { type: Object, default: null },
  editorLabel: { type: String, default: "" },
});
const weekStart = computed(() => props.run?.week_start || null);
const s = useSlaPincodeAlerts(weekStart, () => props.editorLabel);

const show = ref("all"); // all | open | revised
const city = ref("all");
const cities = computed(() => [...new Set(s.alerts.value.map((r) => r.city))].sort());
const rows = computed(() =>
  s.alerts.value
    .filter((r) => (city.value === "all" || r.city === city.value)
      && (show.value === "all" || (show.value === "revised") === s.isRevised(r.pincode)))
    .sort((a, b) => a.on_time_pct - b.on_time_pct || b.late - a.late || a.pincode.localeCompare(b.pincode)));
const revisedCount = computed(() => s.alerts.value.filter((r) => s.isRevised(r.pincode)).length);

const weekLabel = computed(() => {
  const w = s.week.value;
  if (!w) return "";
  const d = new Date(`${w}T00:00:00Z`);
  const f = (x) => x.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
  return `${props.run?.week_no ? `Week ${props.run.week_no} · ` : ""}${f(d)} – ${f(new Date(d.getTime() + 6 * 86400000))}`;
});
const stamp = computed(() => (s.syncedAt.value ? new Date(s.syncedAt.value).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–"));
const n1 = (v) => (v == null ? "–" : `${(+v).toFixed(1)}d`);

function exportCsv() {
  downloadCsv(`sla_change_alerts_${s.week.value}.csv`,
    ["Pincode", "City", "Orders", "Late", "On-time %", "Avg promised TAT", "Avg actual TAT", "SLA revised"],
    rows.value.map((r) => [r.pincode, r.city, r.orders, r.late, r.on_time_pct, r.avg_promised_tat, r.avg_actual_tat, s.isRevised(r.pincode) ? "Yes" : "No"]));
}
</script>

<template>
  <div class="hc-stamps"><span>Alerts {{ stamp }}</span></div>
  <div v-if="s.loadError.value" class="form-error">Couldn't load: {{ s.loadError.value }}</div>
  <div v-if="s.saveError.value" class="form-error">{{ s.saveError.value }}</div>

  <section class="table-card hc-view">
    <div class="sla-card-head" style="padding:12px 14px 0;">
      <h3 class="card-caption" style="padding:0;">SLA change alerts <span class="hc-muted" style="font-weight:500;">· {{ weekLabel }}</span></h3>
      <div class="tbl-tools">
        <select v-model="city"><option value="all">All cities</option><option v-for="c in cities" :key="c" :value="c">{{ c }}</option></select>
        <div class="hc-toggle">
          <button :class="{ active: show === 'all' }" @click="show = 'all'">All</button>
          <button :class="{ active: show === 'open' }" @click="show = 'open'">Not revised</button>
          <button :class="{ active: show === 'revised' }" @click="show = 'revised'">Revised</button>
        </div>
        <span class="count">{{ s.alerts.value.length }} pincodes · {{ revisedCount }} revised</span>
        <button class="btn" :disabled="!rows.length" @click="exportCsv">Download CSV</button>
      </div>
    </div>

    <div v-if="s.loaded.value && !s.alerts.value.length" class="empty-state">No pincode below 88% on time for this week yet.</div>
    <div v-else class="table-scroll scroll-y" style="margin-top:10px;">
      <table class="hc-table nowrap-table">
        <thead><tr>
          <th>Pincode</th><th>City</th><th class="c">Orders</th><th class="c">Late</th><th class="c">On-time</th>
          <th class="c">Avg promised TAT</th><th class="c">Avg actual TAT</th><th class="c">SLA revised</th>
        </tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.pincode">
            <td class="mono">{{ r.pincode }}</td>
            <td>{{ r.city }}</td>
            <td class="c hc-num">{{ r.orders }}</td>
            <td class="c hc-num">{{ r.late }}</td>
            <td class="pc"><span class="hc-pill" :class="otdCls(+r.on_time_pct)">{{ (+r.on_time_pct).toFixed(1) }}%</span></td>
            <td class="c hc-num">{{ n1(r.avg_promised_tat) }}</td>
            <td class="c hc-num">{{ n1(r.avg_actual_tat) }}</td>
            <td class="c">
              <div class="hc-toggle">
                <button :class="{ active: s.isRevised(r.pincode) }" @click="s.setRevised(r.pincode, true)">Yes</button>
                <button :class="{ active: !s.isRevised(r.pincode) }" @click="s.setRevised(r.pincode, false)">No</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
