<script setup>
// Logistics Health Card: the portal's landing section. Views stack one below another with
// no switcher (the user asked for this on 2026-09-25). A new workstream view is one more
// component in the stack.
//   Inventory Risk Monitor  S&OP stock/DOI tables        (useHealthInventoryRisk.js)
//   SLA & Demand Share      sla_trend_weekly, RO + Locks (useHealthSlaData.js)
//   SLA Adherence           sla_trend_weekly LSP split   (useHealthSlaData.js)
//   Spares Inventory        Spares › Summary table       (useSparesData.js)
//   Delayed Orders          health_delay_weekly (+ health_delay_orders for CSV) (useHealthDelayData.js)
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

  <HealthInventoryRisk :groups="inv.groups.value" :totals="inv.totals.value" />
  <HealthSlaDemand :ro="sla.ro.value" :locks="sla.locks.value" />
  <HealthSlaAdherence :ro="sla.ro.value" :locks="sla.locks.value" />
  <SparesSummary :store="spares" title="Spares Inventory" />
  <HealthDelayedOrders :spares="delay.spares.value" :refresh-kit="delay.refreshKit.value" :fetch-orders="delay.fetchDelayedOrders" />
</template>
