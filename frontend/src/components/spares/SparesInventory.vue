<script setup>
// Spares Inventory: one row per SKU that is Ongoing at >=1 planning warehouse. Warehouses where
// it isn't Ongoing show "–" and are left out of the totals. DRR / in transit / delivery date /
// next dispatch come from the sheet; DOI and Required qty are recomputed on clubbed Uniware
// good stock (GGN+Pataudi, KOL+Panchla) -- the sheet's own figures ignore Pataudi/Panchla.
// The Inventory group shows that same clubbed Uniware good stock, so DOI = Inventory / DRR.
import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import { WAREHOUSES, DOI_TARGET, BUCKETS } from "../../composables/useSparesData.js";

const props = defineProps({ store: { type: Object, required: true } });
const s = props.store;

const q = ref("");
const vendorFilter = ref("All");
// Summary's DOI buckets: a SKU matches if any of its Ongoing warehouses falls in the bucket.
const doiFilter = ref("All");

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
      category: s.categoryOf(sku),
      cells,
      totalGood: live.reduce((t, c) => t + c.good, 0),
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

// Category checkboxes: catSel null = all; otherwise the Set of ticked categories ("" = blank).
const catSel = ref(null);
const catOpen = ref(false);
const catEl = ref(null);
const catStyle = ref({});
// .table-card clips overflow, so the panel is fixed to the button (like the vendor picker) and closes on scroll.
function openCats() {
  const r = catEl.value?.getBoundingClientRect();
  if (r) catStyle.value = { top: `${r.bottom + 4}px`, left: `${r.left}px` };
  catOpen.value = !catOpen.value;
}
const onDocScroll = (e) => { if (catOpen.value && !catEl.value?.contains(e.target)) catOpen.value = false; };
const catChoices = computed(() => {
  const counts = new Map();
  for (const r of ongoingRows.value) counts.set(r.category, (counts.get(r.category) || 0) + 1);
  return [...counts].map(([c, n]) => ({ c, n })).sort((a, b) => (a.c === "") - (b.c === "") || a.c.localeCompare(b.c));
});
watch(catChoices, (v) => {
  if (!catSel.value) return;
  const live = new Set(v.map((x) => x.c));
  const kept = new Set([...catSel.value].filter((c) => live.has(c)));
  if (kept.size !== catSel.value.size) catSel.value = kept.size ? kept : null;
});
const catChecked = (c) => !catSel.value || catSel.value.has(c);
function toggleCat(c) {
  const cur = catSel.value ? new Set(catSel.value) : new Set(catChoices.value.map((x) => x.c));
  cur.has(c) ? cur.delete(c) : cur.add(c);
  catSel.value = cur.size === catChoices.value.length ? null : cur;
}
const toggleAllCats = () => { catSel.value = catSel.value ? null : new Set(); };
const catLabel = computed(() => {
  if (!catSel.value) return "All categories";
  if (!catSel.value.size) return "No categories";
  if (catSel.value.size === 1) { const c = [...catSel.value][0]; return c || "(blank)"; }
  return `${catSel.value.size} categories`;
});
const onDocDown = (e) => { if (catOpen.value && catEl.value && !catEl.value.contains(e.target)) catOpen.value = false; };
const onDocKey = (e) => { if (e.key === "Escape") catOpen.value = false; };
onMounted(() => { document.addEventListener("mousedown", onDocDown); document.addEventListener("keydown", onDocKey); window.addEventListener("scroll", onDocScroll, true); });
onUnmounted(() => { document.removeEventListener("mousedown", onDocDown); document.removeEventListener("keydown", onDocKey); window.removeEventListener("scroll", onDocScroll, true); });

const rows = computed(() => ongoingRows.value.filter((r) =>
  (vendorFilter.value === "All" || r.vendor === vendorFilter.value) &&
  (!catSel.value || catSel.value.has(r.category)) &&
  (doiFilter.value === "All" || r.cells.some((c) => c && c.bucket === doiFilter.value))));
const doiDim = (c) => doiFilter.value !== "All" && c.bucket !== doiFilter.value;

const doiClass = (c) => {
  if (!c || c.doi == null) return "";
  if (c.good <= 0 || c.doi <= 7) return "cell-critical";
  if (c.doi <= 15) return "cell-open";
  if (c.doi <= 30) return "cell-warn";
  return "cell-good";
};
// Inventory cells: red ≤ 10 units, yellow ≤ 20 units at that warehouse (same pills as DOI).
const INV_LOW = 10;
const INV_WARN = 20;
const invClass = (c) =>
  c.good <= INV_LOW ? "sp-doi cell-critical" : c.good <= INV_WARN ? "sp-doi cell-warn" : "";
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
      <div ref="catEl" class="sp-multi">
        <button type="button" class="sp-select sp-multi-btn" @click="openCats">{{ catLabel }} ▾</button>
        <div v-if="catOpen" class="sp-multi-panel" :style="catStyle">
          <label class="sp-multi-all"><input type="checkbox" :checked="!catSel" :indeterminate.prop="!!catSel && catSel.size > 0" @change="toggleAllCats" /> All</label>
          <label v-for="x in catChoices" :key="x.c"><input type="checkbox" :checked="catChecked(x.c)" @change="toggleCat(x.c)" />
            <span :class="{ dim: !x.c }">{{ x.c || "(blank)" }}</span><span class="sp-multi-n">{{ x.n }}</span></label>
        </div>
      </div>
      <select v-model="doiFilter" class="sp-select">
        <option value="All">All DOI</option>
        <option v-for="b in BUCKETS" :key="b.key" :value="b.key">{{ b.key === "stockout" ? b.label : `${b.label} DOI` }}</option>
      </select>
      <input v-model="q" class="sp-search" type="search" placeholder="Search SKU ID" />
    </div>
    <div class="table-scroll">
      <table class="sp-table sp-wide">
        <thead>
          <tr>
            <th rowspan="2" class="sp-sticky">SKU</th>
            <th rowspan="2">Vendor</th>
            <th rowspan="2">Category</th>
            <th :colspan="WAREHOUSES.length + 1" class="grp">Inventory (Uniware)</th>
            <th :colspan="WAREHOUSES.length + 1" class="grp">DRR</th>
            <th :colspan="WAREHOUSES.length" class="grp">DOI</th>
            <th :colspan="WAREHOUSES.length" class="grp">In transit</th>
            <th :colspan="WAREHOUSES.length" class="grp">Delivery Date</th>
            <th colspan="2" class="grp">Next dispatch</th>
            <th :colspan="WAREHOUSES.length + 1" class="grp">Required qty basis {{ DOI_TARGET }} DOI</th>
          </tr>
          <tr>
            <th v-for="(w, i) in WAREHOUSES" :key="'g' + w.key" class="num" :class="{ gl: i === 0 }">{{ w.label }}</th>
            <th class="num">Total</th>
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
            <td :class="{ dim: !r.category }">{{ r.category || "–" }}</td>

            <td v-for="(c, i) in r.cells" :key="'g' + i" class="num hc-num" :class="{ gl: i === 0 }">
              <span v-if="c" :class="invClass(c)">{{ c.good > 0 ? fmt(c.good) : "0" }}</span><span v-else class="dash">–</span>
            </td>
            <td class="num hc-num"><b>{{ fmt(r.totalGood) }}</b></td>

            <td v-for="(c, i) in r.cells" :key="'d' + i" class="num hc-num" :class="{ gl: i === 0 }">
              <span v-if="c">{{ fmtDrr(c.drr) }}</span><span v-else class="dash">–</span>
            </td>
            <td class="num hc-num"><b>{{ fmtDrr(r.totalDrr) }}</b></td>

            <td v-for="(c, i) in r.cells" :key="'o' + i" class="c" :class="{ gl: i === 0 }">
              <span v-if="c" class="sp-doi" :class="[doiClass(c), { 'sp-doi-dim': doiDim(c) }]">{{ fmtDoi(c) }}</span><span v-else class="dash">–</span>
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
          <tr v-if="!rows.length"><td :colspan="37" class="dim">No Ongoing spares{{ q || vendorFilter !== "All" || doiFilter !== "All" || catSel ? " match these filters" : "" }}.</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
