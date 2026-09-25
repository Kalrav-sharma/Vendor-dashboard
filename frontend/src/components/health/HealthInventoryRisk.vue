<script setup>
// Health Card › 01 Inventory Risk Monitor. Rows are the four network tiers; columns are
// the three mutually exclusive risk bands. Each tile counts facility × SKU pairs, and
// clicking one lists exactly which pairs they are.
import { ref, computed } from "vue";
import { BANDS } from "../../composables/useHealthInventoryRisk.js";

const props = defineProps({
  groups: { type: Array, required: true },
  totals: { type: Object, required: true },
});

const sel = ref(null); // { group, band }
const BAND_COLOR = { stockout: "var(--risk-so)", lt7: "var(--risk-7)", lt15: "var(--risk-15)" };

function tile(g, b) {
  if (g.doiNa && b !== "stockout") return { cls: "na", num: "–", of: "no sales history yet", clickable: false };
  const n = g[b];
  if (!n) return { cls: "zero", num: "0", of: "all clear", clickable: false };
  const share = g.monitored ? n / g.monitored : 0;
  const a = Math.round(12 + Math.min(1, share * 2.5) * 58);
  return { cls: `b-${b}${a > 46 ? " hot" : ""}`, num: String(n), of: `of ${g.monitored}`, clickable: true, style: { "--a": `${a}%` } };
}
function pick(g, b) {
  if (!tile(g, b).clickable) return;
  sel.value = sel.value && sel.value.group === g.key && sel.value.band === b ? null : { group: g.key, band: b };
}
const detail = computed(() => {
  if (!sel.value) return null;
  const g = props.groups.find(x => x.key === sel.value.group);
  if (!g) return null;
  const b = BANDS.find(x => x.key === sel.value.band);
  const pairs = g.pairs.filter(p => p.band === sel.value.band)
    .sort((x, y) => (x.doi ?? -1) - (y.doi ?? -1) || x.where.localeCompare(y.where));
  return { g, b, pairs, color: BAND_COLOR[sel.value.band] };
});
const fmtDrr = v => (v == null ? "–" : Number(v) >= 10 ? Math.round(v) : Number(v).toFixed(1));
const fmtDoi = v => (v == null ? "–" : v < 10 ? v.toFixed(1) : Math.round(v));
const meter = g => [
  { w: g.monitored ? (g.stockout / g.monitored) * 100 : 0, c: "var(--risk-so)" },
  { w: g.monitored ? (g.lt7 / g.monitored) * 100 : 0, c: "var(--risk-7)" },
  { w: g.monitored ? (g.lt15 / g.monitored) * 100 : 0, c: "var(--risk-15)" },
];
</script>

<template>
  <div class="hc-view-head">
    <div>
      <div class="hc-step">01 · Inventory Risk Monitor</div>
      <h3>How many SKU-locations are at risk</h3>
      <p class="desc">Each facility × SKU is counted in exactly one band. Click a number to see which ones.</p>
    </div>
  </div>

  <section class="hc-card">
    <div class="risk-grid">
      <div class="h">Network tier</div>
      <div v-for="b in BANDS" :key="b.key" class="h" style="justify-content:center;">
        <span class="sw" :style="{ background: BAND_COLOR[b.key] }"></span>{{ b.label }} <small>· {{ b.sub }}</small>
      </div>
      <div class="h hmon" style="justify-content:flex-end;">At risk</div>

      <template v-for="g in groups" :key="g.key">
        <div class="rowlab"><span class="n">{{ g.label }}</span><span class="s">{{ g.sub }}</span></div>
        <div v-for="b in BANDS" :key="b.key" class="cell">
          <button type="button" class="risk-tile" :class="[tile(g, b.key).cls, { sel: sel && sel.group === g.key && sel.band === b.key }]"
                  :style="tile(g, b.key).style" :disabled="!tile(g, b.key).clickable" @click="pick(g, b.key)">
            <span class="num">{{ tile(g, b.key).num }}</span>
            <span class="of">{{ tile(g, b.key).of }}</span>
          </button>
        </div>
        <div class="mon">
          <span class="big">{{ g.atRisk }}<span style="color:var(--muted); font-weight:400;"> / {{ g.monitored }}</span></span>
          <span class="meter"><span v-for="(m, i) in meter(g)" :key="i" :style="{ width: m.w + '%', background: m.c }"></span></span>
          <span v-if="g.noDoi && !g.doiNa" class="lbl">{{ g.noDoi }} with no recent sales</span>
        </div>
      </template>

      <div class="rowlab total last"><span class="n">Network</span><span class="s">all tiers</span></div>
      <div v-for="b in BANDS" :key="b.key" class="cell total last" style="display:flex; align-items:center; justify-content:center;">
        <span class="mono" :style="{ fontSize: '1.15rem', fontWeight: 700, color: totals[b.key] ? BAND_COLOR[b.key] : 'var(--risk-ok)' }">{{ totals[b.key] }}</span>
      </div>
      <div class="mon total last"><span class="big">{{ totals.atRisk }}<span style="color:var(--muted); font-weight:400;"> / {{ totals.monitored }}</span></span></div>
    </div>

    <div v-if="detail" class="risk-detail">
      <div class="risk-detail-head">
        <strong>{{ detail.g.label }} · {{ detail.b.label }} <span style="color:var(--muted); font-weight:400;">({{ detail.pairs.length }})</span></strong>
        <button class="x" @click="sel = null">Close</button>
      </div>
      <div class="risk-chips">
        <div v-for="p in detail.pairs" :key="(p.code || p.where) + p.sku" class="risk-chip" :style="{ '--c': detail.color }">
          <div><div class="w">{{ p.sku }}</div><div class="k">{{ p.where }}</div></div>
          <div class="d">
            <template v-if="detail.b.key === 'stockout'">0 units<small>{{ p.drr ? `DRR ${fmtDrr(p.drr)}/day` : "no recent sales" }}</small></template>
            <template v-else>{{ fmtDoi(p.doi) }}d<small>{{ p.onHand }} units · DRR {{ fmtDrr(p.drr) }}/day</small></template>
          </div>
        </div>
      </div>
    </div>
  </section>

  <p class="hc-foot">
    DOI = on-hand ÷ daily run-rate: 10-day DRR for warehouses, 15-day for dark stores. MT uses its forward-forecast DOI.
    MFCs haven't sold yet, so only stockouts are scored there. A pair with stock but no recent sales isn't counted as at risk.
    Jhilmil and Sohna are excluded.
  </p>
</template>
