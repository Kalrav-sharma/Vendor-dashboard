<script setup>
// Logistics Health Card: the portal's landing section. "Sync now" (HealthSyncButton.vue)
// starts the RO + Spares inventory syncs on demand. Views stack one below another with
// no switcher (the user asked for this on 2026-09-25). A new workstream view is one more
// component in the stack. Each numbered section collapses from its header (HealthSection.vue).
//   1. Inventory View
//     1.1 RO Inventory      S&OP stock/DOI tables        (useHealthInventoryRisk.js)
//     1.2 Spares Inventory  Spares › Summary table       (useSparesData.js)
//   2. Delivery Experience
//     2.1 SLA & Demand Share sla_trend_weekly, RO + Locks (useHealthSlaData.js)
//     2.2 SLA Adherence     RO | Locks, sla_trend_weekly LSP split (useHealthSlaData.js)
//     2.3 Spares & Refresh SLA  beside 2.2, by city tier, health_sla_kit_weekly (useHealthKitSlaData.js)
//     (Delayed Orders moved to Spares › Spares Delivery on 2026-10-05)
//   3. Payment Pendency    (admin/management/finance only -- canSeePaymentDashboard)
//     3.1 Overdue Payments  po_invoice_uploads via useInvoiceUploads (loaded by AdminApp)
//     3.2 Payment Outlook   due now / due in 7d / stuck in 3-way recon (usePaymentPendency.js)
import "./health.css";
import "../spares/spares.css";
import HealthSection from "./HealthSection.vue";
import HealthSyncButton from "./HealthSyncButton.vue";
import HealthInventoryRisk from "./HealthInventoryRisk.vue";
import HealthSlaDemand from "./HealthSlaDemand.vue";
import HealthSlaAdherence from "./HealthSlaAdherence.vue";
import HealthPaymentPendency from "./HealthPaymentPendency.vue";
import HealthPaymentOutlook from "./HealthPaymentOutlook.vue";
import SparesSummary from "../spares/SparesSummary.vue";
import { useHealthInventoryRisk } from "../../composables/useHealthInventoryRisk.js";
import { useHealthSlaData } from "../../composables/useHealthSlaData.js";
import { useHealthKitSlaData } from "../../composables/useHealthKitSlaData.js";
import { useSparesData } from "../../composables/useSparesData.js";
import { useInvoiceUploads } from "../../composables/useInvoiceUploads.js";

const props = defineProps({
  showPayments: { type: Boolean, default: false }, // AdminApp's canSeePaymentDashboard
  vendorLabel: { type: Function, default: code => code },
});

const inv = useHealthInventoryRisk();
const sla = useHealthSlaData();
const kitSla = useHealthKitSlaData();
const { allUploads } = useInvoiceUploads(); // singleton, fetched by AdminApp on login
const spares = useSparesData(null); // read-only here: Appendix edits stay in the Spares section

const stamp = t => (t ? new Date(t).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");
const refreshAll = () => Promise.all([inv.refresh(), spares.refresh()]);
const stale = (t, h) => !t || (Date.now() - new Date(t).getTime()) / 3600000 > h;
</script>

<template>
  <div class="hc-stamps">
    <HealthSyncButton @done="refreshAll" />
    <span :class="{ stale: stale(inv.stockSyncedAt.value, 14) }">Stock {{ stamp(inv.stockSyncedAt.value) }}</span>
    <span :class="{ stale: stale(sla.lastSynced.value, 30) }">SLA {{ stamp(sla.lastSynced.value) }}</span>
    <span :class="{ stale: stale(kitSla.lastSynced.value, 30) }">Spares/Refresh SLA {{ stamp(kitSla.lastSynced.value) }}</span>
    <span :class="{ stale: stale(spares.stockSyncedAt.value, 3) || stale(spares.drrSyncedAt.value, 30) }">Spares {{ stamp(spares.stockSyncedAt.value) }}</span>
  </div>

  <div v-if="inv.loadError.value || sla.loadError.value || kitSla.loadError.value || spares.loadError.value" class="form-error">
    Couldn't load: {{ inv.loadError.value || sla.loadError.value || kitSla.loadError.value || spares.loadError.value }}
  </div>

  <HealthSection id="inventory" title="1. Inventory View">
    <div class="hc-pair hc-view hc-pair-views">
      <HealthInventoryRisk :groups="inv.groups.value" :totals="inv.totals.value" title="1.1 RO Inventory" />
      <SparesSummary :store="spares" title="1.2 Spares Inventory" />
    </div>
  </HealthSection>

  <HealthSection id="delivery" title="2. Delivery Experience">
    <HealthSlaDemand :ro="sla.ro.value" :locks="sla.locks.value" title="2.1 SLA & Demand Share" />
    <!-- 2.2 RO | Locks on the left (the width of 2.1's SLA table), 2.3 Spares | Refresh beside it -->
    <div class="hc-pair hc-view hc-pair-views">
      <HealthSlaAdherence :ro="sla.ro.value" :locks="sla.locks.value" :products="['ro', 'locks']" title="2.2 SLA Adherence" />
      <HealthSlaAdherence :ro="[]" :locks="[]" :spares="kitSla.spares.value" :refresh-kit="kitSla.refreshKit.value" :products="['spares', 'refresh']" title="2.3 Spares & Refresh SLA" />
    </div>
  </HealthSection>

  <HealthSection v-if="props.showPayments" id="payments" title="3. Payment Pendency">
    <HealthPaymentPendency title="3.1 Overdue Payments" :rows="allUploads" :vendor-label="props.vendorLabel" />
    <HealthPaymentOutlook title="3.2 Payment Outlook" :rows="allUploads" :vendor-label="props.vendorLabel" />
  </HealthSection>
</template>
