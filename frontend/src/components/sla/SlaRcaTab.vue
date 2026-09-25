<script setup>
// SLA › Week N — RCA. The /late-delivery-rca dashboard, live in the portal. It has the
// same four tabs (Overview, RCA Breakdown, SLA View, Open SLA Breaches), rendered from
// the newest sla_rca_run payload (parse_late_delivery_rca.js --portal-json).
//
// The sync chooses the week: Wed–Sun shows the current week, Mon/Tue the previous one.
// Shadowfax classification comes from the most recent interactive /late-delivery-rca run,
// since it's browser-only. DTDC and BlueDart are classified live through their APIs.
import { ref, computed } from "vue";
import SlaRcaOverview from "./SlaRcaOverview.vue";
import SlaRcaBreakdown from "./SlaRcaBreakdown.vue";
import SlaRcaSlaView from "./SlaRcaSlaView.vue";
import SlaRcaOpenBreaches from "./SlaRcaOpenBreaches.vue";

const props = defineProps({
  run: { type: Object, default: null },
  loaded: { type: Boolean, default: false },
  loadError: { type: String, default: "" },
});

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "rca", label: "RCA Breakdown" },
  { id: "sla", label: "SLA View" },
  { id: "open", label: "Open SLA Breaches" },
];
const tab = ref("overview");
const p = computed(() => props.run?.payload || null);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const range = computed(() => {
  if (!props.run) return "";
  const s = new Date(`${props.run.week_start}T00:00:00Z`);
  const e = new Date(s.getTime() + 6 * 86400000);
  const f = d => `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
  return `${f(s)} – ${f(e)}`;
});
const generated = computed(() => (props.run ? new Date(props.run.generated_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""));
const stale = computed(() => props.run && (Date.now() - new Date(props.run.generated_at).getTime()) / 3600000 > 30);
// The promised-delivery week hasn't ended yet (its Sunday is today or later).
const inProgress = computed(() => props.run && new Date(`${props.run.week_start}T00:00:00+05:30`).getTime() + 7 * 86400000 > Date.now());
const sfxAge = computed(() => {
  const t = props.run?.shadowfax_map_as_of;
  if (!t) return "no Shadowfax map available: Shadowfax dockets unclassified";
  return `Shadowfax map from ${new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}`;
});
</script>

<template>
  <div v-if="loadError" class="form-error">Couldn't load the RCA: {{ loadError }}</div>
  <div v-else-if="!loaded" class="sla-card loading">Loading…</div>
  <div v-else-if="!run" class="sla-card empty-state">No RCA run yet. It appears after the first SLA sync runs.</div>

  <template v-else>
    <section class="rca-banner">
      <div>
        <div class="sla-eyebrow">Late-delivery root-cause analysis</div>
        <div class="wk" style="margin-top:6px;">Week {{ run.week_no }}<small>{{ range }}</small></div>
        <div class="meta">
          Promised-delivery week · {{ inProgress ? "in progress, still maturing" : "complete" }} · {{ sfxAge }}
        </div>
      </div>
      <span class="fresh" :class="{ stale }">generated {{ generated }}</span>
    </section>

    <div class="rca-inner-tabs">
      <button v-for="t in TABS" :key="t.id" class="sla-view-pill" :class="{ active: tab === t.id }" @click="tab = t.id">{{ t.label }}</button>
    </div>

    <!-- wrapper divs: v-show on a multi-root component silently does nothing -->
    <div v-show="tab === 'overview'"><SlaRcaOverview :p="p" /></div>
    <div v-show="tab === 'rca'"><SlaRcaBreakdown :p="p" /></div>
    <div v-show="tab === 'sla'"><SlaRcaSlaView :p="p" /></div>
    <div v-show="tab === 'open'"><SlaRcaOpenBreaches :p="p" /></div>
  </template>
</template>
