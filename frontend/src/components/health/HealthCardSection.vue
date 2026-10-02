<script setup>
// Logistics Health Card: the portal's landing section. Views stack one below another with
// no switcher (the user asked for this on 2026-09-25). A new workstream view is one more
// component in the stack.
//   1. Inventory View
//     1.1 RO Inventory      S&OP stock/DOI tables        (useHealthInventoryRisk.js)
//     1.2 Spares Inventory  Spares › Summary table       (useSparesData.js)
//   2. Delivery Experience
//     2.1 SLA & Demand Share sla_trend_weekly, RO + Locks (useHealthSlaData.js)
//     2.2 SLA Adherence     sla_trend_weekly LSP split   (useHealthSlaData.js)
//     2.3 Delayed Orders          health_delay_weekly (+ health_delay_orders for CSV) (useHealthDelayData.js)
import "./health.css";
import "../spares/spares.css";
import HealthInventoryRisk from "./HealthInventoryRisk.vue";
import HealthSlaDemand from "./HealthSlaDemand.vue";
import HealthSlaAdherence from "./HealthSlaAdherence.vue";
import HealthDelayedOrders from "./HealthDelayedOrders.vue";
import SparesSummary from "../spares/SparesSummary.vue";
import { useHealthInventoryRisk } from "../../composables/useHealthInventoryRisk.js";
import { useHealthSlaData } from "../../composables/useHealthSlaData.js";
import { useHealthDelayData } from "../../composables/useHealthDelayData.js";
import { useSparesData } from "../../composables/useSparesData.js";

const inv = useHealthInventoryRisk();
const sla = useHealthSlaData();
const delay = useHealthDelayData();
const spares = useSparesData(null); // read-only here: Appendix edits stay in the Spares section

const stamp = t => (t ? new Date(t).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");
const stale = (t, h) => !t || (Date.now() - new Date(t).getTime()) / 3600000 > h;
</script>

<template>
  <div class="hc-stamps">
    <span :class="{ stale: stale(inv.stockSyncedAt.value, 14) }">Stock {{ stamp(inv.stockSyncedAt.value) }}</span>
    <span :class="{ stale: stale(sla.lastSynced.value, 30) }">SLA {{ stamp(sla.lastSynced.value) }}</span>
    <span :class="{ stale: stale(delay.lastSynced.value, 30) }">Delays {{ stamp(delay.lastSynced.value) }}</span>
    <span :class="{ stale: stale(spares.stockSyncedAt.value, 3) || stale(spares.drrSyncedAt.value, 30) }">Spares {{ stamp(spares.stockSyncedAt.value) }}</span>
  </div>

  <div v-if="inv.loadError.value || sla.loadError.value || delay.loadError.value || spares.loadError.value" class="form-error">
    Couldn't load: {{ inv.loadError.value || sla.loadError.value || delay.loadError.value || spares.loadError.value }}
  </div>

  <h2 class="hc-section-title">1. Inventory View</h2>
  <div class="hc-pair hc-view hc-pair-views">
    <HealthInventoryRisk :groups="inv.groups.value" :totals="inv.totals.value" title="1.1 RO Inventory" />
    <SparesSummary :store="spares" title="1.2 Spares Inventory" />
  </div>
  <h2 class="hc-section-title">2. Delivery Experience</h2>
  <HealthSlaDemand :ro="sla.ro.value" :locks="sla.locks.value" title="2.1 SLA & Demand Share" />
  <!-- single child: 2.2 takes the left column, the width of 2.1's SLA table -->
  <div class="hc-pair hc-view hc-pair-views">
    <HealthSlaAdherence :ro="sla.ro.value" :locks="sla.locks.value" title="2.2 SLA Adherence" />
  </div>
  <HealthDelayedOrders title="2.3 Delayed Orders" :spares="delay.spares.value" :refresh-kit="delay.refreshKit.value" :fetch-orders="delay.fetchDelayedOrders" />
</template>
