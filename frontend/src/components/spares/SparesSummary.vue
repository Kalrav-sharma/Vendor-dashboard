<script setup>
// Summary: count of spares per DOI bucket x planning warehouse (GGN+Pataudi, KOL+Panchla
// clubbed) -- the sheet's "Spares excluding IK & Discontinued" table, restricted to
// SKU x warehouse pairs marked Ongoing in Appendix. Click a count for the SKUs behind it.
import { ref, computed } from "vue";
import { WAREHOUSES, BUCKETS } from "../../composables/useSparesData.js";

const props = defineProps({
  store: { type: Object, required: true },
  title: { type: String, default: "Spares excluding IK & Discontinued" },
});
const s = props.store;

const PILL = { stockout: "cell-critical", "0-7": "cell-critical", "8-15": "cell-open", "16-30": "cell-warn", "31-60": "cell-good", ">60": "" };

// pairs[bucket][whKey] = [{ sku, wh, fig }]
const pairs = computed(() => {
  const out = Object.fromEntries(BUCKETS.map((b) => [b.key, Object.fromEntries(WAREHOUSES.map((w) => [w.key, []]))]));
  for (const sku of s.allSkus.value) {
    if (!s.inSummaryScope(sku)) continue;
    for (const wh of WAREHOUSES) {
      if (!s.whOngoing(sku, wh)) continue;
      const fig = s.whFigures(sku, wh);
      if (fig.bucket) out[fig.bucket][wh.key].push({ sku, wh, fig });
    }
  }
  return out;
});

const colTotal = computed(() => Object.fromEntries(WAREHOUSES.map((w) => [w.key, BUCKETS.reduce((t, b) => t + pairs.value[b.key][w.key].length, 0)])));
const rowTotal = (b) => WAREHOUSES.reduce((t, w) => t + pairs.value[b][w.key].length, 0);
const grand = computed(() => BUCKETS.reduce((t, b) => t + rowTotal(b.key), 0));
const pct = (n) => (grand.value ? `${Math.round((n / grand.value) * 100)}%` : "–");

const sel = ref(null); // { b, w } -- w null = all warehouses
function pick(b, w) {
  const same = sel.value && sel.value.b === b && sel.value.w === w;
  sel.value = same ? null : { b, w };
}
const detail = computed(() => {
  if (!sel.value) return null;
  const { b, w } = sel.value;
  const whs = w ? WAREHOUSES.filter((x) => x.key === w) : WAREHOUSES;
  const rows = whs.flatMap((x) => pairs.value[b][x.key]).map((p) => ({ ...p, supply: s.supplyStatus(p.sku, p.wh), vendor: s.vendorOf(p.sku) }));
  rows.sort((a, c) => (a.fig.doi ?? -1) - (c.fig.doi ?? -1) || a.sku.localeCompare(c.sku));
  return { title: `${BUCKETS.find((x) => x.key === b).label} · ${w ? WAREHOUSES.find((x) => x.key === w).label : "All warehouses"}`, rows };
});

const fmt = (n) => (n == null ? "–" : Math.round(n).toLocaleString("en-IN"));
const fmtDrr = (n) => (n > 0 ? (+n).toFixed(1) : "–");
</script>

<template>
  <section class="table-card hc-view">
    <h3 class="card-caption">{{ title }}</h3>
    <div class="table-scroll">
      <table class="hc-table">
        <colgroup><col style="width:14%"><col v-for="w in WAREHOUSES" :key="w.key"><col style="width:11%"><col style="width:9%"></colgroup>
        <thead><tr>
          <th>DOI</th>
          <th v-for="w in WAREHOUSES" :key="w.key" class="c">{{ w.label }}</th>
          <th class="c">Total</th>
          <th class="c">%</th>
        </tr></thead>
        <tbody>
          <tr v-for="b in BUCKETS" :key="b.key">
            <td class="lab">{{ b.label }}</td>
            <td v-for="w in WAREHOUSES" :key="w.key" class="pc">
              <button type="button" class="hc-pill"
                      :class="[pairs[b.key][w.key].length ? PILL[b.key] : 'na', { click: pairs[b.key][w.key].length, sel: sel && sel.b === b.key && sel.w === w.key }]"
                      :disabled="!pairs[b.key][w.key].length" @click="pick(b.key, w.key)">{{ pairs[b.key][w.key].length }}</button>
            </td>
            <td class="pc">
              <button type="button" class="hc-pill" :class="{ click: rowTotal(b.key), na: !rowTotal(b.key), sel: sel && sel.b === b.key && sel.w === null }"
                      :disabled="!rowTotal(b.key)" @click="pick(b.key, null)">{{ rowTotal(b.key) }}</button>
            </td>
            <td class="c hc-num hc-muted">{{ pct(rowTotal(b.key)) }}</td>
          </tr>
          <tr class="row-total">
            <td class="lab">Total</td>
            <td v-for="w in WAREHOUSES" :key="w.key" class="c hc-num">{{ colTotal[w.key] }}</td>
            <td class="c hc-num">{{ grand }}</td>
            <td></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="detail" class="hc-detail">
      <div class="hc-detail-head">
        <span>{{ detail.title }} ({{ detail.rows.length }})</span>
        <button @click="sel = null">Close</button>
      </div>
      <div class="table-scroll">
        <table class="sp-table">
          <thead><tr>
            <th>SKU ID</th><th>Vendor</th><th>WH</th><th class="num">Stock</th><th class="num">DRR</th><th class="num">DOI</th><th>Status</th>
          </tr></thead>
          <tbody>
            <tr v-for="r in detail.rows" :key="r.sku + r.wh.key">
              <td class="sku">{{ r.sku }}</td>
              <td>{{ r.vendor }}</td>
              <td>{{ r.wh.label }}</td>
              <td class="num hc-num">{{ fmt(r.fig.good) }}</td>
              <td class="num hc-num">{{ fmtDrr(r.fig.drr) }}</td>
              <td class="num hc-num">{{ r.fig.doi == null ? "–" : Math.floor(r.fig.doi) }}</td>
              <td><span class="sp-tag" :class="r.supply.kind">{{ r.supply.text }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
