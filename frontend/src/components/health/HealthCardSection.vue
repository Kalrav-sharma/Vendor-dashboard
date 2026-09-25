<script setup>
// Logistics Health Card: the portal's first and landing section. It shows the few
// signals that say whether the network is healthy right now, one numbered view per
// workstream. A new workstream is a new VIEWS entry plus its component.
//   01 Inventory Risk Monitor: S&OP stock/DOI tables (useHealthInventoryRisk.js)
//   02 SLA & Demand Share:     sla_trend_weekly, RO + Locks (useHealthSlaData.js)
// The hero's signal cards double as the view switcher.
import { ref, computed } from "vue";
import "../sla/sla.css";
import "./health.css";
import HealthInventoryRisk from "./HealthInventoryRisk.vue";
import HealthSlaDemand from "./HealthSlaDemand.vue";
import { useHealthInventoryRisk } from "../../composables/useHealthInventoryRisk.js";
import { useHealthSlaData } from "../../composables/useHealthSlaData.js";

const inv = useHealthInventoryRisk();
const sla = useHealthSlaData();
const view = ref("inventory");

const stamp = t => (t ? new Date(t).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");
const stale = (t, h) => !t || (Date.now() - new Date(t).getTime()) / 3600000 > h;

// Headline signal per view
const invSignal = computed(() => {
  const t = inv.totals.value;
  const lvl = t.stockout > 20 ? "bad" : t.atRisk ? "warn" : "";
  return { v: `${t.stockout} stockouts`, t: `${t.atRisk} of ${t.monitored} SKU-locations at risk`, lvl };
});
const slaSignal = computed(() => {
  // last complete week = the first "past" row
  const w = sla.ro.value.find(x => x.kind === "past");
  const s = w?.tiers.pan.sla;
  return { v: s == null ? "–" : `${s.toFixed(2)}d`, t: w ? `RO Pan India SLA · week ${w.weekNo}` : "awaiting SLA sync", lvl: s != null && s > 2.5 ? "warn" : "" };
});
const VIEWS = computed(() => [
  { id: "inventory", ix: "01", label: "Inventory Risk Monitor", sig: invSignal.value },
  { id: "sla", ix: "02", label: "SLA & Demand Share", sig: slaSignal.value },
]);
</script>

<template>
  <section class="hc-hero">
    <div class="hc-hero-top">
      <div>
        <div class="sla-eyebrow">Logistics Health Card</div>
        <h2>Network health at a glance</h2>
        <p class="lede">Stock risk across warehouses, dark stores, MFCs and MT, and delivery speed by city tier, for RO and Locks.</p>
      </div>
      <div class="hc-fresh">
        <span class="fresh" :class="{ stale: stale(inv.stockSyncedAt.value, 14) }">stock {{ stamp(inv.stockSyncedAt.value) }}</span>
        <span class="fresh" :class="{ stale: stale(sla.lastSynced.value, 30) }">SLA {{ stamp(sla.lastSynced.value) }}</span>
      </div>
    </div>
    <div class="hc-signals">
      <button v-for="v in VIEWS" :key="v.id" type="button" class="hc-signal" :class="{ active: view === v.id }" @click="view = v.id">
        <span class="dotlive" :class="v.sig.lvl"></span>
        <span style="display:flex; flex-direction:column; gap:2px; min-width:0;">
          <span class="t"><span class="ix">{{ v.ix }}</span> · {{ v.label }}</span>
          <span class="v">{{ v.sig.v }}</span>
          <span class="t" style="font-size:.72rem;">{{ v.sig.t }}</span>
        </span>
      </button>
    </div>
  </section>

  <div v-if="inv.loadError.value || sla.loadError.value" class="form-error">Couldn't load: {{ inv.loadError.value || sla.loadError.value }}</div>

  <div v-show="view === 'inventory'"><HealthInventoryRisk :groups="inv.groups.value" :totals="inv.totals.value" /></div>
  <div v-show="view === 'sla'"><HealthSlaDemand :ro="sla.ro.value" :locks="sla.locks.value" /></div>
</template>
