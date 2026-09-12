<script setup>
// S&OP -- live replica of the /sop-master Claude Code dashboard, as a
// sub-tabbed section inside AdminApp.vue (admin/management/operations
// only, see canSeeSop there). Each sub-tab is its own component backed by
// its own Supabase-polling composable (see components/sop/*).
//
// All sections mount at once (v-show, not v-if, per sub-tab) so a
// background poll keeps running while another sub-tab is showing --
// same convention AdminApp.vue itself uses for its top-level nav.
import { ref } from "vue";
import SopInventoryTab from "./SopInventoryTab.vue";

const SUBTABS = [
  { id: "inventory", label: "Inventory Overview" },
  // Sales, Day-on-Day Sales, Production Plan, PO Fulfillment, Channel
  // Dispatch Plan land here in later phases.
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

  <div v-show="activeSubTab === 'inventory'"><SopInventoryTab /></div>
</template>
