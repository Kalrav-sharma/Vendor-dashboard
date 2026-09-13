<script setup>
// S&OP > Day-on-Day Sales -- 6 stacked date x SKU/channel tables (By SKU,
// By Channel, By SKU-UC, By SKU-Amazon, By SKU-Flipkart, By SKU-MT -- the
// last one is a bonus beyond /sop-master's original 5, since the source
// data turned out to already carry it). Date window: current month's
// elapsed days, padded backward to a minimum of 7 rows -- same logic as
// the original /sop-master tab.
import { computed } from "vue";
import { useSopDailySalesData } from "../../composables/useSopDailySalesData.js";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const CHANNELS = ["UC App+PLS", "MT", "Amazon", "Flipkart", "Others"];
const MIN_ROWS = 7;

const SECTIONS = [
  { series: "by_sku", title: "By SKU (all channels)", dims: SKUS },
  { series: "by_channel", title: "By Channel", dims: CHANNELS },
  { series: "by_sku_uc", title: "By SKU -- UC App+PLS only", dims: SKUS },
  { series: "by_sku_amazon", title: "By SKU -- Amazon only", dims: SKUS },
  { series: "by_sku_flipkart", title: "By SKU -- Flipkart only", dims: SKUS },
  { series: "by_sku_mt", title: "By SKU -- MT only", dims: SKUS },
];

const { rows, loadError } = useSopDailySalesData();

function fmt(n) {
  return Math.round(n || 0).toLocaleString("en-IN");
}

function addDaysYMD(ymd, days) {
  const [y, m, d] = ymd.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}

function dateLabel(ymd) {
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "UTC" });
}

const dateWindow = computed(() => {
  const todayYMD = new Date().toISOString().slice(0, 10);
  const monthStart = todayYMD.slice(0, 7) + "-01";
  const dates = [];
  let d = monthStart;
  while (d <= todayYMD) { dates.push(d); d = addDaysYMD(d, 1); }
  if (dates.length < MIN_ROWS) {
    const need = MIN_ROWS - dates.length;
    const prevDates = [];
    let pd = addDaysYMD(monthStart, -1);
    for (let i = 0; i < need; i++) { prevDates.unshift(pd); pd = addDaysYMD(pd, -1); }
    return prevDates.concat(dates);
  }
  return dates;
});

const bySeries = computed(() => {
  const grouped = {};
  for (const r of rows.value) {
    grouped[r.series] = grouped[r.series] || {};
    grouped[r.series][r.sale_date] = grouped[r.series][r.sale_date] || {};
    grouped[r.series][r.sale_date][r.dim] = r.qty;
  }
  return grouped;
});

const tables = computed(() => SECTIONS.map(section => {
  const bySale = bySeries.value[section.series] || {};
  const body = dateWindow.value.map(ymd => {
    const dayData = bySale[ymd] || {};
    const cells = section.dims.map(d => dayData[d] || 0);
    return { ymd, cells, total: cells.reduce((s, v) => s + v, 0) };
  });
  const totalRow = {
    cells: section.dims.map((_, i) => body.reduce((s, r) => s + r.cells[i], 0)),
    total: body.reduce((s, r) => s + r.total, 0),
  };
  return { ...section, body, totalRow };
}));
</script>

<template>
  <div v-if="loadError" class="form-error">{{ loadError }}</div>

  <template v-for="t in tables" :key="t.series">
    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">{{ t.title }}</h3>
    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead><tr><th>Date</th><th v-for="d in t.dims" :key="d" class="num">{{ d }}</th><th class="num">Total</th></tr></thead>
        <tbody>
          <tr v-for="r in t.body" :key="r.ymd">
            <td>{{ dateLabel(r.ymd) }}</td>
            <td v-for="(c, i) in r.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
            <td class="num mono"><b>{{ fmt(r.total) }}</b></td>
          </tr>
          <tr style="font-weight: 600;">
            <td>Total</td>
            <td v-for="(c, i) in t.totalRow.cells" :key="i" class="num mono">{{ fmt(c) }}</td>
            <td class="num mono">{{ fmt(t.totalRow.total) }}</td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </template>
</template>
