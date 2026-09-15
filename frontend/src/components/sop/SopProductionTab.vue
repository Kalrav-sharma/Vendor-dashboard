<script setup>
// S&OP > Production Plan -- existing Planned/Actual/Delta (COMBINED,
// network-wide) tables, unchanged in shape from /sop-master, PLUS a
// simplified date x facility-total view (Amber / Ronch / Combined, summed
// across all SKUs) the user specifically asked for. A view selector picks
// one group at a time instead of stacking every table on screen.
//
// The "planned" figure for a past date is read from production_plan_snapshots
// (frozen the morning of that date, before the sheet's own Actual
// Production cell could flip from placeholder-plan to true-actual) rather
// than sop_production_daily's live planned_qty, which may have already
// flipped by the time anyone views this page. The Planned Production
// table heading says "(snapshot)" to signal this rather than tagging
// individual rows/cells.
import { computed, ref } from "vue";
import { useSopProductionData } from "../../composables/useSopProductionData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const FACILITY_SPLIT_RELIABLE_FROM = "2026-08-01";
const VIEWS = [
  { key: "planned-actual", label: "Planned vs Actual" },
  { key: "by-facility", label: "By Facility" },
];

const { dailyRows, snapshotRows, loadError } = useSopProductionData();

const activeView = ref(VIEWS[0].key);

function fmt(n) {
  return n == null ? "–" : Math.round(n).toLocaleString("en-IN");
}

function dateLabel(ymd) {
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "UTC" });
}

const todayYMD = new Date().toISOString().slice(0, 10);
const monthStart = todayYMD.slice(0, 7) + "-01";

function addDaysYMD(ymd, days) {
  const [y, m, d] = ymd.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}
function monthEndYMD(ymd) {
  const [y, m] = ymd.split("-").map(Number);
  return new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10);
}

// Current month's full date range -- this portal has the whole history in Supabase already (no
// need for /sop-master's "pinned end date, extend manually" workaround), so just show the month.
const dateWindow = computed(() => {
  const dates = [];
  let d = monthStart;
  const end = monthEndYMD(monthStart);
  while (d <= end) { dates.push(d); d = addDaysYMD(d, 1); }
  return dates;
});

// prod_date -> sku -> facility -> {planned_qty, actual_qty}
const byDateSkuFacility = computed(() => {
  const map = {};
  for (const r of dailyRows.value) {
    map[r.prod_date] = map[r.prod_date] || {};
    map[r.prod_date][r.sku] = map[r.prod_date][r.sku] || {};
    map[r.prod_date][r.sku][r.facility] = r;
  }
  return map;
});
// snapshot_date -> sku -> facility -> planned_qty
const snapshotMap = computed(() => {
  const map = {};
  for (const r of snapshotRows.value) {
    map[r.snapshot_date] = map[r.snapshot_date] || {};
    map[r.snapshot_date][r.sku] = map[r.snapshot_date][r.sku] || {};
    map[r.snapshot_date][r.sku][r.facility] = r.planned_qty;
  }
  return map;
});

// Resolves the planned figure for one (date, sku, facility): a frozen snapshot if one exists,
// else the live (possibly not-yet-final) figure.
function resolvedPlanned(ymd, sku, facility) {
  const snap = (snapshotMap.value[ymd] || {})[sku]?.[facility];
  if (snap !== undefined) return snap;
  return (byDateSkuFacility.value[ymd] || {})[sku]?.[facility]?.planned_qty ?? null;
}

const combinedTable = computed(() => {
  return dateWindow.value.map(ymd => {
    const perSku = SKUS.map(sku => {
      const planned = resolvedPlanned(ymd, sku, "COMBINED");
      const actual = (byDateSkuFacility.value[ymd] || {})[sku]?.COMBINED?.actual_qty ?? 0;
      return { sku, planned, actual };
    });
    return {
      ymd,
      plannedTotal: perSku.reduce((s, r) => s + (r.planned || 0), 0),
      actualTotal: perSku.reduce((s, r) => s + (r.actual || 0), 0),
      perSku,
    };
  });
});

// A facility usually only produces 1-2 SKUs on a given day -- a blind summed total hid which
// SKU(s) that was. Comma-joined "SKU: qty" text (nonzero SKUs only) instead; Combined stays a
// plain total since it's genuinely a network-wide figure, not one facility's own SKU mix.
function facilityBreakdown(ymd, facility) {
  const parts = SKUS
    .map(sku => ({ sku, qty: (byDateSkuFacility.value[ymd] || {})[sku]?.[facility]?.actual_qty ?? 0 }))
    .filter(p => p.qty > 0);
  return parts.length ? parts.map(p => `${p.sku}: ${fmt(p.qty)}`).join(", ") : "–";
}

const facilitySummaryTable = computed(() => dateWindow.value.map(ymd => {
  let combined = 0;
  for (const sku of SKUS) combined += (byDateSkuFacility.value[ymd] || {})[sku]?.COMBINED?.actual_qty ?? 0;
  return { ymd, ronch: facilityBreakdown(ymd, "RONCH"), amber: facilityBreakdown(ymd, "AMBER"), combined };
}));

const showPreAugCaveat = computed(() => dateWindow.value.some(d => d < FACILITY_SPLIT_RELIABLE_FROM));

const kpiTiles = computed(() => {
  const plannedTotal = combinedTable.value.reduce((s, r) => s + r.plannedTotal, 0);
  const actualTotal = combinedTable.value.reduce((s, r) => s + r.actualTotal, 0);
  return [
    { label: "Planned (month)", value: fmt(plannedTotal) },
    { label: "Actual (month)", value: fmt(actualTotal) },
    { label: "Delta", value: fmt(actualTotal - plannedTotal), cls: actualTotal - plannedTotal < 0 ? "critical" : "good" },
  ];
});
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div v-if="loadError" class="form-error">{{ loadError }}</div>

  <div class="subtabs" style="margin-bottom: 18px;">
    <button
      v-for="v in VIEWS" :key="v.key"
      class="subtab-item" :class="{ active: activeView === v.key }"
      @click="activeView = v.key"
    >{{ v.label }}</button>
  </div>

  <div v-show="activeView === 'planned-actual'">
    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Planned Production (snapshot)</h3>
    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead><tr><th>Date</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
        <tbody>
          <tr v-for="r in combinedTable" :key="r.ymd">
            <td>{{ dateLabel(r.ymd) }}</td>
            <td v-for="p in r.perSku" :key="p.sku" class="num mono">{{ fmt(p.planned) }}</td>
            <td class="num mono"><b>{{ fmt(r.plannedTotal) }}</b></td>
          </tr>
        </tbody>
      </table>
    </div></div>

    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Actual Production (network-wide)</h3>
    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead><tr><th>Date</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
        <tbody>
          <tr v-for="r in combinedTable" :key="r.ymd">
            <td>{{ dateLabel(r.ymd) }}</td>
            <td v-for="p in r.perSku" :key="p.sku" class="num mono">{{ fmt(p.actual) }}</td>
            <td class="num mono"><b>{{ fmt(r.actualTotal) }}</b></td>
          </tr>
        </tbody>
      </table>
    </div></div>

    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Delta (Actual - Planned)</h3>
    <div class="table-card"><div class="table-scroll">
      <table>
        <thead><tr><th>Date</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
        <tbody>
          <tr v-for="r in combinedTable" :key="r.ymd">
            <td>{{ dateLabel(r.ymd) }}</td>
            <td v-for="p in r.perSku" :key="p.sku" class="num mono" :class="r.ymd < todayYMD ? { critical: (p.actual - (p.planned||0)) < 0, good: (p.actual - (p.planned||0)) > 0 } : {}">
              {{ fmt(p.actual - (p.planned || 0)) }}
            </td>
            <td class="num mono"><b>{{ fmt(r.actualTotal - r.plannedTotal) }}</b></td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </div>

  <div v-show="activeView === 'by-facility'">
    <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Actual Production by facility (all SKUs combined)</h3>
    <p v-if="showPreAugCaveat" class="chip chip-muted" style="display: inline-block; margin-bottom: 12px;">
      Facility split was unmaintained before Aug 2026 -- treat pre-Aug-2026 Ronch/Amber figures as unreliable.
    </p>
    <div class="table-card"><div class="table-scroll">
      <table>
        <thead><tr><th>Date</th><th>Ronch</th><th>Amber</th><th class="num">Combined</th></tr></thead>
        <tbody>
          <tr v-for="r in facilitySummaryTable" :key="r.ymd">
            <td>{{ dateLabel(r.ymd) }}</td>
            <td class="mono">{{ r.ronch }}</td>
            <td class="mono">{{ r.amber }}</td>
            <td class="num mono"><b>{{ fmt(r.combined) }}</b></td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </div>
</template>
