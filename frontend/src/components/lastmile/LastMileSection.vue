<script setup>
// Last Mile Tracking -- warehouse-to-customer delivery visibility,
// modelled on a working operational report ("Shipment Watch") that
// already covers Blue Dart, Delhivery, DTDC, Holisol and Shadowfax
// across Native's D2C/UC-App channels. Sub-tabbed inside AdminApp.vue,
// same convention as S&OP (see SopSection.vue) -- each sub-tab is its
// own component backed by the same useLastMileData.js composable
// (Supabase-polling), fed by scripts/sync_last_mile.py.
//
// Alerts is first and the default tab -- it's the actionable queue;
// everything else here is context for it.
//
// All sections mount at once (v-show, not v-if) so a background poll
// keeps running while another sub-tab is showing -- same convention
// AdminApp.vue itself uses for its top-level nav.
import { ref } from "vue";
import LastMileAlertsTab from "./LastMileAlertsTab.vue";
import LastMileCarrierTab from "./LastMileCarrierTab.vue";
import LastMileWorstLanesTab from "./LastMileWorstLanesTab.vue";
import LastMileCoverageTab from "./LastMileCoverageTab.vue";

const SUBTABS = [
  { id: "alerts", label: "Alerts" },
  { id: "carrier", label: "Carrier Performance" },
  { id: "lanes", label: "Worst Lanes" },
  { id: "coverage", label: "Coverage & Data Quality" },
];

const activeSubTab = ref(SUBTABS[0].id);
</script>

<template>
  <div class="subtabs">
    <button
      v-for="t in SUBTABS" :key="t.id"
      class="subtab-item" :class="{ active: activeSubTab === t.id }"
      @click="activeSubTab = t.id"
    >{{ t.label }}</button>
  </div>

  <div v-show="activeSubTab === 'alerts'"><LastMileAlertsTab /></div>
  <div v-show="activeSubTab === 'carrier'"><LastMileCarrierTab /></div>
  <div v-show="activeSubTab === 'lanes'"><LastMileWorstLanesTab /></div>
  <div v-show="activeSubTab === 'coverage'"><LastMileCoverageTab /></div>
</template>
