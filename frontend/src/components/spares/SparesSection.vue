<script setup>
// Spares: Summary / Spares Inventory / Warehouse stock / Spares Delivery / Appendix, over the "Spare automations"
// sheet + live Uniware good/bad stock (scripts/sync_spares.py), DRR from Jarvis 485614 (spares_drr). Appendix is the decision layer:
// its per-SKU x warehouse status and per-SKU vendor drive the other three views.
import { ref } from "vue";
import "../health/health.css";
import "./spares.css";
import { useSparesData } from "../../composables/useSparesData.js";
import SparesSummary from "./SparesSummary.vue";
import SparesInventory from "./SparesInventory.vue";
import SparesWarehouseStock from "./SparesWarehouseStock.vue";
import SparesAppendix from "./SparesAppendix.vue";
import SparesDelivery from "./SparesDelivery.vue";
import { useHealthDelayData } from "../../composables/useHealthDelayData.js";

const props = defineProps({ editorLabel: { type: String, default: "" } });
const store = useSparesData(() => props.editorLabel);
const { sheetSyncedAt, stockSyncedAt, drrSyncedAt, loadError, saveError } = store;
const delay = useHealthDelayData(); // Spares Delivery tab (health_delay_weekly / health_delay_orders)

const SUBTABS = [
  { id: "summary", label: "Summary" },
  { id: "inventory", label: "Spares Inventory" },
  { id: "stock", label: "Warehouse stock" },
  { id: "delivery", label: "Spares Delivery" },
  { id: "appendix", label: "Appendix" },
];
const activeSubTab = ref("summary");

const fmtStamp = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");
const stale = (iso) => !iso || Date.now() - new Date(iso).getTime() > 3 * 60 * 60 * 1000;
// DRR comes from the local launchd sync (10:45 / 16:45 IST), not the half-hourly Action.
const staleDrr = (iso) => !iso || Date.now() - new Date(iso).getTime() > 30 * 60 * 60 * 1000;
</script>

<template>
  <div class="subtabs">
    <button v-for="t in SUBTABS" :key="t.id" class="subtab-item" :class="{ active: activeSubTab === t.id }"
            @click="activeSubTab = t.id">{{ t.label }}</button>
  </div>

  <div class="hc-stamps">
    <span :class="{ stale: stale(sheetSyncedAt) }">Sheet {{ fmtStamp(sheetSyncedAt) }}</span>
    <span :class="{ stale: stale(stockSyncedAt) }">Uniware stock {{ fmtStamp(stockSyncedAt) }}</span>
    <span :class="{ stale: staleDrr(drrSyncedAt) }">DRR {{ fmtStamp(drrSyncedAt) }}</span>
    <span v-if="activeSubTab === 'delivery'" :class="{ stale: staleDrr(delay.lastSynced.value) }">Delays {{ fmtStamp(delay.lastSynced.value) }}</span>
  </div>

  <div v-if="loadError" class="sp-alert">{{ loadError }}</div>
  <div v-if="saveError" class="sp-alert">{{ saveError }}</div>
  <div v-if="activeSubTab === 'delivery' && delay.loadError.value" class="sp-alert">{{ delay.loadError.value }}</div>

  <div v-show="activeSubTab === 'summary'">
    <SparesSummary :store="store" />
    <SparesInventory :store="store" :buckets="['stockout', '0-7']" summary-scope title="Stock out & 0-7 DOI spares" />
  </div>
  <div v-show="activeSubTab === 'inventory'"><SparesInventory :store="store" /></div>
  <div v-show="activeSubTab === 'stock'"><SparesWarehouseStock :store="store" /></div>
  <div v-show="activeSubTab === 'delivery'"><SparesDelivery :spares="delay.spares.value" :refresh-kit="delay.refreshKit.value" :fetch-orders="delay.fetchDelayedOrders" /></div>
  <div v-show="activeSubTab === 'appendix'"><SparesAppendix :store="store" /></div>
</template>
