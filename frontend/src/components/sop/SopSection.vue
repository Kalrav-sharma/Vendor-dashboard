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
import SopSalesTab from "./SopSalesTab.vue";
import SopDailySalesTab from "./SopDailySalesTab.vue";
import SopProductionTab from "./SopProductionTab.vue";
import SopPoFulfillmentTab from "./SopPoFulfillmentTab.vue";
import SopDispatchPlanTab from "./SopDispatchPlanTab.vue";

const SUBTABS = [
  { id: "inventory", label: "Inventory Overview" },
  { id: "sales", label: "Sales: Plan vs Actual" },
  { id: "daily-sales", label: "Day-on-Day Sales" },
  { id: "production", label: "Production Plan" },
  { id: "po-fulfillment", label: "PO Fulfillment" },
  { id: "dispatch-plan", label: "S&OP Planning" },
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
  <div v-show="activeSubTab === 'sales'"><SopSalesTab /></div>
  <div v-show="activeSubTab === 'daily-sales'"><SopDailySalesTab /></div>
  <div v-show="activeSubTab === 'production'"><SopProductionTab /></div>
  <div v-show="activeSubTab === 'po-fulfillment'"><SopPoFulfillmentTab /></div>
  <div v-show="activeSubTab === 'dispatch-plan'"><SopDispatchPlanTab /></div>
</template>
