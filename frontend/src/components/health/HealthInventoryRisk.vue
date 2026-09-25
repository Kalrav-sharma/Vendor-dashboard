<script setup>
// Health Card › Inventory Risk Monitor. Tiers × mutually exclusive bands (Stockout,
// <7 DOI, 7–<15 DOI), counting facility × SKU pairs. Click a count to list its pairs.
import { ref, computed } from "vue";
import { BANDS } from "../../composables/useHealthInventoryRisk.js";

const props = defineProps({
  groups: { type: Array, required: true },
  totals: { type: Object, required: true },
});
const TINT = { stockout: "cell-critical", lt7: "cell-open", lt15: "cell-warn" };
const sel = ref(null);

function pill(g, b) {
  if (g.doiNa && b !== "stockout") return { cls: "na", txt: "–", click: false };
  const n = g[b];
  return n ? { cls: `${TINT[b]} click`, txt: n, click: true } : { cls: "cell-good", txt: 0, click: false };
}
function pick(g, b) {
  if (!pill(g, b).click) return;
  sel.value = sel.value?.g === g.key && sel.value?.b === b ? null : { g: g.key, b };
}
const detail = computed(() => {
  if (!sel.value) return null;
  const g = props.groups.find(x => x.key === sel.value.g);
  const b = BANDS.find(x => x.key === sel.value.b);
  if (!g || !b) return null;
  const pairs = g.pairs.filter(p => p.band === sel.value.b)
    .sort((x, y) => (x.doi ?? -1) - (y.doi ?? -1) || x.where.localeCompare(y.where));
  return { g, b, pairs };
});
const fmtDoi = v => (v == null ? "" : `${v < 10 ? v.toFixed(1) : Math.round(v)}d`);
</script>

<template>
  <section class="table-card hc-view">
    <h3 class="card-caption">Inventory Risk Monitor</h3>
    <div class="table-scroll">
      <table class="hc-table">
        <colgroup><col style="width:26%"><col><col><col><col style="width:16%"></colgroup>
        <thead><tr>
          <th>Tier</th>
          <th v-for="b in BANDS" :key="b.key" class="c">{{ b.label }}</th>
          <th class="num">At risk</th>
        </tr></thead>
        <tbody>
          <tr v-for="g in groups" :key="g.key">
            <td class="lab">{{ g.label }}<small>{{ g.sub }}</small></td>
            <td v-for="b in BANDS" :key="b.key" class="pc">
              <button type="button" class="hc-pill" :class="[pill(g, b.key).cls, { sel: sel && sel.g === g.key && sel.b === b.key }]"
                      :disabled="!pill(g, b.key).click" @click="pick(g, b.key)">{{ pill(g, b.key).txt }}</button>
            </td>
            <td class="num hc-num">{{ g.atRisk }} <span class="hc-muted">/ {{ g.monitored }}</span></td>
          </tr>
          <tr class="row-total">
            <td class="lab">Network</td>
            <td v-for="b in BANDS" :key="b.key" class="c hc-num">{{ totals[b.key] }}</td>
            <td class="num hc-num">{{ totals.atRisk }} <span class="hc-muted">/ {{ totals.monitored }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="detail" class="hc-detail">
      <div class="hc-detail-head">
        <span>{{ detail.g.label }} · {{ detail.b.label }} ({{ detail.pairs.length }})</span>
        <button @click="sel = null">Close</button>
      </div>
      <div class="hc-chips">
        <span v-for="p in detail.pairs" :key="(p.code || p.where) + p.sku" class="hc-chip">
          <b>{{ p.sku }}</b> · {{ p.where }}<span v-if="detail.b.key !== 'stockout'" class="hc-num">{{ fmtDoi(p.doi) }}</span>
        </span>
      </div>
    </div>
  </section>
</template>
