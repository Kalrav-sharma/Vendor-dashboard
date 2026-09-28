<script setup>
// Spares Inventory: one row per SKU that is Ongoing at >=1 planning warehouse. Warehouses where
// it isn't Ongoing show "–" and are left out of the totals. DRR / in transit / delivery date /
// next dispatch come from the sheet; DOI and Required qty are recomputed on clubbed Uniware
// good stock (GGN+Pataudi, KOL+Panchla) -- the sheet's own figures ignore Pataudi/Panchla.
import { ref, computed, watch } from "vue";
import { WAREHOUSES, DOI_TARGET } from "../../composables/useSparesData.js";

const props = defineProps({ store: { type: Object, required: true } });
const s = props.store;

const q = ref("");
const vendorFilter = ref("All");

// Every Ongoing row before the vendor filter -- the filter's own options come from these, so a
// vendor typed in Appendix (existing or brand new) shows up here as soon as it's saved.
const ongoingRows = computed(() => {
  const needle = q.value.trim().toLowerCase();
  const out = [];
  for (const sku of s.allSkus.value) {
    if (needle && !sku.toLowerCase().includes(needle)) continue;
    const on = WAREHOUSES.map((w) => s.whOngoing(sku, w));
    if (!on.some(Boolean)) continue;
    const m = s.masterBySku.value.get(sku);
    const cells = WAREHOUSES.map((w, i) => (on[i] ? s.whFigures(sku, w) : null));
    const live = cells.filter(Boolean);
    out.push({
      sku,
      vendor: s.vendorOf(sku),
      cells,
      totalDrr: live.reduce((t, c) => t + c.drr, 0),
      totalRequired: live.reduce((t, c) => t + c.required, 0),
      nextDate: m?.next_dispatch_date || null,
      nextQty: m?.next_dispatch_qty ?? null,
    });
  }
  return out;
});

const vendorChoices = computed(() => {
  const set = new Set(ongoingRows.value.map((r) => r.vendor));
  const hasNa = set.delete("NA");
  return [...[...set].sort((a, b) => a.localeCompare(b)), ...(hasNa ? ["NA"] : [])];
});
watch(vendorChoices, (v) => { if (vendorFilter.value !== "All" && !v.includes(vendorFilter.value)) vendorFilter.value = "All"; });

const rows = computed(() => (vendorFilter.value === "All" ? ongoingRows.value : ongoingRows.value.filter((r) => r.vendor === vendorFilter.value)));

const doiClass = (c) => {
  if (!c || c.doi == null) return "";
  if (c.good <= 0 || c.doi <= 7) return "cell-critical";
  if (c.doi <= 15) return "cell-open";
  if (c.doi <= 30) return "cell-warn";
  return "cell-good";
};
const fmt = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-IN"));
const fmtDrr = (n) => (n > 0 ? (+n).toFixed(1) : "0");
const fmtDoi = (c) => (c.good <= 0 ? "0" : c.doi == null ? "∞" : Math.floor(c.doi));
const deliveryText = (c) => {
  if (!c.delivery) return "–";
  if (/grn\s*pending/i.test(c.delivery)) return "GRN Pending";
  return c.delivery;
};
</script>

<template>
  <section class="table-card hc-view">
    <div class="sp-toolbar">
      <h3 class="card-caption" style="padding:0;border:none">Spares Inventory</h3>
      <span class="grow"></span>
      <span class="sp-count"><b>{{ rows.length }}</b> ongoing SKUs</span>
      <select v-model="vendorFilter" class="sp-select">
        <option value="All">All vendors</option>
        <option v-for="v in vendorChoices" :key="v" :value="v">{{ v }}</option>
      </select>
      <input v-model="q" class="sp-search" type="search" placeholder="Search SKU ID" />
    </div>
    <div class="table-scroll">
      <table class="sp-table sp-wide">
        <thead>
          <tr>
            <th rowspan="2" class="sp-sticky">SKU</th>
            <th rowspan="2">Vendor</th>
            <th :colspan="WAREHOUSES.length + 1" class="grp">DRR</th>
            <th :colspan="WAREHOUSES.length" class="grp">DOI</th>
            <th :colspan="WAREHOUSES.length" class="grp">In transit</th>
            <th :colspan="WAREHOUSES.length" class="grp">Delivery Date</th>
            <th colspan="2" class="grp">Next dispatch</th>
            <th :colspan="WAREHOUSES.length + 1" class="grp">Required qty basis {{ DOI_TARGET }} DOI</th>
          </tr>
          <tr>
            <th v-for="(w, i) in WAREHOUSES" :key="'d' + w.key" class="num" :class="{ gl: i === 0 }">{{ w.label }}</th>
            <th class="num">Total</th>
            <th v-for="(w, i) in WAREHOUSES" :key="'o' + w.key" class="c" :class="{ gl: i === 0 }">{{ w.label }}</th>
            <th v-for="(w, i) in WAREHOUSES" :key="'t' + w.key" class="num" :class="{ gl: i === 0 }">{{ w.label }}</th>
            <th v-for="(w, i) in WAREHOUSES" :key="'e' + w.key" class="c" :class="{ gl: i === 0 }">{{ w.label }}</th>
            <th class="c gl">Date</th>
            <th class="num">Qty</th>
            <th v-for="(w, i) in WAREHOUSES" :key="'r' + w.key" class="num" :class="{ gl: i === 0 }">{{ w.label }}</th>
            <th class="num">Total</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.sku">
            <td class="sku sp-sticky">{{ r.sku }}</td>
            <td :class="{ dim: r.vendor === 'NA' }">{{ r.vendor }}</td>

            <td v-for="(c, i) in r.cells" :key="'d' + i" class="num hc-num" :class="{ gl: i === 0 }">
              <span v-if="c">{{ fmtDrr(c.drr) }}</span><span v-else class="dash">–</span>
            </td>
            <td class="num hc-num"><b>{{ fmtDrr(r.totalDrr) }}</b></td>

            <td v-for="(c, i) in r.cells" :key="'o' + i" class="c" :class="{ gl: i === 0 }">
              <span v-if="c" class="sp-doi" :class="doiClass(c)">{{ fmtDoi(c) }}</span><span v-else class="dash">–</span>
            </td>

            <td v-for="(c, i) in r.cells" :key="'t' + i" class="num hc-num" :class="{ gl: i === 0 }">
              <span v-if="c && c.inTransit">{{ fmt(c.inTransit) }}</span><span v-else class="dash">–</span>
            </td>

            <td v-for="(c, i) in r.cells" :key="'e' + i" class="c" :class="{ gl: i === 0 }">
              <template v-if="c && c.delivery">
                <span v-if="deliveryText(c) === 'GRN Pending'" class="sp-tag grn">GRN Pending</span>
                <span v-else>{{ deliveryText(c) }}</span>
              </template>
              <span v-else class="dash">–</span>
            </td>

            <td class="c gl">{{ r.nextDate || "–" }}</td>
            <td class="num hc-num">{{ r.nextQty ? fmt(r.nextQty) : "–" }}</td>

            <td v-for="(c, i) in r.cells" :key="'r' + i" class="num hc-num" :class="{ gl: i === 0 }">
              <span v-if="c">{{ c.required ? fmt(c.required) : "0" }}</span><span v-else class="dash">–</span>
            </td>
            <td class="num hc-num"><b>{{ fmt(r.totalRequired) }}</b></td>
          </tr>
          <tr v-if="!rows.length"><td :colspan="30" class="dim">No Ongoing spares{{ q || vendorFilter !== "All" ? " match these filters" : "" }}.</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
