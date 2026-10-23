<script setup>
// Health Card › Net sales by SKU, month-over-month. Same Jarvis SQL as the Native Freight &
// Dispatch console's Sales tab (Native RO, India, net of returns), synced into
// health_sales_monthly by scripts/sync_health_sales.py on a VPN laptop.
// Last MONTHS calendar months, the current one month-to-date. Each month's units are followed
// by that SKU's share of the month; ▲/▼ = share gained/lost vs the previous month.
import { computed } from "vue";
import HealthAsOf from "./HealthAsOf.vue";
import { useHealthSalesData } from "../../composables/useHealthSalesData.js";

const props = defineProps({ title: { type: String, default: "Net sales by SKU · month-over-month" } });

// The console's presentation order. A SKU with no sales in the window is dropped (M1 today);
// an unexpected sku from the sync is appended rather than lost.
const SKU_ORDER = ["M0", "M1", "M1 AS", "M1 Pro", "M2", "M2 AS", "M2 Pro", "M3", "M3 Pro"];
const MONTHS = 4;

const { rows, loadError, lastSynced } = useHealthSalesData();

const months = computed(() => {
  const now = new Date();
  const out = [];
  for (let i = MONTHS - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(now.getFullYear(), now.getMonth() - i, 1));
    out.push({
      key: d.toISOString().slice(0, 7),
      label: d.toLocaleDateString("en-IN", { month: "short", year: "2-digit", timeZone: "UTC" }),
      mtd: i === 0,
    });
  }
  return out;
});

const table = computed(() => {
  const keys = months.value.map(m => m.key);
  const qty = {};
  for (const r of rows.value) {
    const i = keys.indexOf(String(r.month_start).slice(0, 7));
    if (i < 0) continue;
    qty[r.sku] = qty[r.sku] || keys.map(() => 0);
    qty[r.sku][i] += Number(r.net_sales) || 0;
  }
  const SKUS = [...SKU_ORDER.filter(k => qty[k]), ...Object.keys(qty).filter(k => !SKU_ORDER.includes(k)).sort()];
  const monthTot = keys.map((_, i) => SKUS.reduce((s, k) => s + qty[k][i], 0));
  const grand = monthTot.reduce((s, v) => s + v, 0);
  const share = (v, d) => (d ? (v / d) * 100 : 0);
  const body = SKUS.map(sku => {
    const vals = qty[sku];
    const shares = vals.map((v, i) => share(v, monthTot[i]));
    const total = vals.reduce((s, v) => s + v, 0);
    const max = Math.max(...vals.map(Math.abs), 0);
    return {
      sku,
      cells: vals.map((v, i) => ({
        v,
        sh: shares[i],
        dir: i === 0 || !monthTot[i] || !monthTot[i - 1] ? "" : shares[i].toFixed(1) > shares[i - 1].toFixed(1) ? "up" : shares[i].toFixed(1) < shares[i - 1].toFixed(1) ? "down" : "",
      })),
      total,
      totalSh: share(total, grand),
      bars: vals.map(v => (max ? Math.max(2, (Math.max(v, 0) / max) * 22) : 2)),
    };
  });
  return { body, monthTot, grand };
});

const fmt = n => Math.round(n || 0).toLocaleString("en-IN");
const pct = n => `${n.toFixed(1)}%`;
</script>

<template>
  <section class="table-card hc-view">
    <div class="hc-caption">
      <h3 class="card-caption">{{ props.title }}</h3>
      <span class="hc-sales-key">Units + share of month · <span class="hc-dd good">▲</span> share gained / <span class="hc-dd bad">▼</span> lost vs prev month</span>
    </div>
    <HealthAsOf :items="[{ label: 'Jarvis sync', at: lastSynced, staleH: 30 }]" />
    <!-- Until health_sales_monthly exists / first sync runs, say so instead of a raw PostgREST error. -->
    <div v-if="!rows.length" class="card-legend">No sales data yet -- waiting for the first Jarvis sync (scripts/sync_health_sales.py).<span v-if="loadError" class="hc-muted"> ({{ loadError }})</span></div>
    <div v-if="rows.length" class="table-scroll">
      <table class="hc-table hc-sales">
        <thead><tr>
          <th>SKU</th>
          <template v-for="m in months" :key="m.key">
            <th class="num">{{ m.label }}<small v-if="m.mtd" class="hc-muted"> · MTD</small></th>
            <th class="num">%</th>
          </template>
          <th class="num hc-sales-sep">Total</th>
          <th class="num">%</th>
          <th class="num">Trend</th>
        </tr></thead>
        <tbody>
          <tr v-for="r in table.body" :key="r.sku">
            <td class="lab">{{ r.sku }}</td>
            <template v-for="(c, i) in r.cells" :key="i">
              <td class="num hc-num" :class="{ 'hc-muted': !c.v }">{{ fmt(c.v) }}</td>
              <td class="num hc-num hc-sales-sh" :class="c.dir ? (c.dir === 'up' ? 'hc-dd good' : 'hc-dd bad') : 'hc-muted'">
                <span v-if="c.dir">{{ c.dir === "up" ? "▲" : "▼" }} </span>{{ pct(c.sh) }}
              </td>
            </template>
            <td class="num hc-num hc-sales-sep"><b>{{ fmt(r.total) }}</b></td>
            <td class="num hc-num hc-muted">{{ pct(r.totalSh) }}</td>
            <td class="num">
              <span class="hc-spark"><i v-for="(h, i) in r.bars" :key="i" :class="{ last: i === r.bars.length - 1 }" :style="{ height: h + 'px' }"></i></span>
            </td>
          </tr>
          <tr class="row-total">
            <td class="lab">Total</td>
            <template v-for="(t, i) in table.monthTot" :key="i">
              <td class="num hc-num">{{ fmt(t) }}</td>
              <td class="num hc-num">{{ t ? "100%" : "–" }}</td>
            </template>
            <td class="num hc-num hc-sales-sep">{{ fmt(table.grand) }}</td>
            <td class="num hc-num">{{ table.grand ? "100%" : "–" }}</td>
            <td></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="rows.length" class="card-legend">Jarvis · Native RO net sales (India, net of returns), same query as the Freight &amp; Dispatch console. {{ months[months.length - 1].label }} is month-to-date.</div>
  </section>
</template>
