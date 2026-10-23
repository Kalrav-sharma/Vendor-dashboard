<script setup>
// Spares > Projections (BETA, internal): what each vendor is projected to produce and supply over the next
// 3 months, SKU by SKU. Same numbers the vendor projection emails carry (rolling 2-month coverage, netted
// against on-hand + in-transit; Refresh/IK kit parts net month-by-month). Admin / management / operations only.
import { ref, computed } from "vue";
import "../health/health.css";
import "./spares.css";
import HealthAsOf from "../health/HealthAsOf.vue";
import { useSparesProjection } from "../../composables/useSparesProjection.js";

const { loadError, loaded, computedAt, forecastAsof, months, groups } = useSparesProjection();

const vendorFilter = ref("All");
const q = ref("");
const vendorNames = computed(() => groups.value.withOrder.map(g => g.vendor));
const shown = computed(() => {
  const needle = q.value.trim().toLowerCase();
  return groups.value.withOrder
    .filter(g => vendorFilter.value === "All" || g.vendor === vendorFilter.value)
    .map(g => (needle ? { ...g, skus: g.skus.filter(s => s.sku.toLowerCase().includes(needle)) } : g))
    .filter(g => g.skus.length);
});
const grand = computed(() => groups.value.withOrder.reduce((t, g) => t + g.total, 0));
const skuCount = computed(() => groups.value.withOrder.reduce((t, g) => t + g.skus.length, 0));

const fmt = n => Math.round(n || 0).toLocaleString("en-IN");
const asof = computed(() => [
  { label: "Projections computed", at: computedAt.value, staleH: 4 },
  { label: "Demand forecast as of", at: forecastAsof.value ? `${forecastAsof.value}T00:00:00` : null, staleH: 24 * 40 },
]);
</script>

<template>
  <section class="table-card hc-view">
    <div class="hc-caption">
      <h3 class="card-caption">Spares projections · next 3 months <span class="hc-muted">· BETA</span></h3>
      <span class="hc-sales-key">Internal only · not shared with vendors · logic still being tuned</span>
    </div>
    <HealthAsOf :items="asof" />
    <div v-if="loadError" class="sp-alert">{{ loadError }}</div>
    <div v-else-if="loaded && !groups.withOrder.length" class="card-legend">No projections yet -- waiting for the first run of scripts/sync_spares_projection.py.</div>

    <template v-if="groups.withOrder.length">
      <div class="card-legend">
        {{ groups.withOrder.length }} vendor(s) · {{ skuCount }} SKU(s) · {{ fmt(grand) }} units over 3 months.
        <span v-if="groups.covered.length"> Fully covered (no order): {{ groups.covered.join(", ") }}.</span>
        Refresh kit parts are renewal-driven and the renewals projection ends Jan 2027, so their later months read low.
      </div>
      <div class="card-legend">
        <select v-model="vendorFilter" class="sp-select" aria-label="Vendor">
          <option value="All">All vendors</option>
          <option v-for="v in vendorNames" :key="v" :value="v">{{ v }}</option>
        </select>
        <input v-model="q" class="sp-search" type="search" placeholder="Search SKU" />
      </div>
      <div class="table-scroll">
        <table class="hc-table">
          <thead><tr>
            <th>Vendor / SKU</th>
            <th v-for="m in months" :key="m.key" class="num">{{ m.label }}</th>
            <th class="num">3-month total</th>
          </tr></thead>
          <tbody v-for="g in shown" :key="g.vendor">
            <tr class="row-total">
              <td class="lab">{{ g.vendor }} <small class="hc-muted">· {{ g.skus.length }} SKU(s)</small></td>
              <td v-for="m in months" :key="m.key" class="num hc-num">{{ fmt(g.totals[m.key]) }}</td>
              <td class="num hc-num">{{ fmt(g.total) }}</td>
            </tr>
            <tr v-for="s in g.skus" :key="s.sku">
              <td class="lab">{{ s.sku }}</td>
              <td v-for="m in months" :key="m.key" class="num hc-num" :class="{ 'hc-muted': !s.qty[m.key] }">{{ fmt(s.qty[m.key]) }}</td>
              <td class="num hc-num"><b>{{ fmt(s.total) }}</b></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>
