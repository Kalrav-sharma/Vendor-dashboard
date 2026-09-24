<script setup>
// SLA: delivery SLA performance, brought into the portal from two local Claude skills:
//   Trends       <- /late-delivery-trends (leadership trend lines, weekly/monthly)
//   Week N — RCA <- /late-delivery-rca (the week's late-delivery root-cause dashboard)
// Both are fed from Jarvis by a VPN-side sync (~/.claude/scripts/sla_portal/), because
// GitHub Actions can't reach Jarvis. See schema.sql's "SLA section" block.
//
// The RCA sub-tab's label carries the week actually on display. The sync applies the
// rule (Wed–Sun: current week; Mon/Tue: previous week, still maturing) and stores the
// week number with the run, so the label always matches the data under it.
import { ref, computed } from "vue";
import "./sla.css";
import SlaTrendsTab from "./SlaTrendsTab.vue";
import SlaRcaTab from "./SlaRcaTab.vue";
import { useSlaRcaData } from "../../composables/useSlaRcaData.js";

const { run, loaded, loadError } = useSlaRcaData();

const SUBTABS = computed(() => [
  { id: "trends", label: "Trends" },
  { id: "rca", label: run.value ? `Week ${run.value.week_no} — RCA` : "Week — RCA" },
]);
const activeSubTab = ref("trends");
</script>

<template>
  <div class="subtabs">
    <button
      v-for="t in SUBTABS" :key="t.id"
      class="subtab-item" :class="{ active: activeSubTab === t.id }"
      @click="activeSubTab = t.id"
    >{{ t.label }}</button>
  </div>

  <div v-show="activeSubTab === 'trends'"><SlaTrendsTab :rca-run="run" /></div>
  <div v-show="activeSubTab === 'rca'"><SlaRcaTab :run="run" :loaded="loaded" :load-error="loadError" /></div>
</template>
