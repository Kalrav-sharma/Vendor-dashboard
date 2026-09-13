<script setup>
// S&OP > Production Plan -- existing Planned/Actual/Delta (COMBINED,
// network-wide) tables, unchanged in shape from /sop-master, PLUS a new
// date x SKU facility-split view (Amber / Ronch / Combined) the user
// specifically asked for.
//
// The "planned" figure for a past date is read from production_plan_snapshots
// (frozen the morning of that date, before the sheet's own Actual
// Production cell could flip from placeholder-plan to true-actual) rather
// than sop_production_daily's live planned_qty, which may have already
// flipped by the time anyone views this page. Any date without a
// snapshot yet (today, or before this sync was deployed) falls back to
// the live figure with a visible "provisional" chip.
import { computed } from "vue";
import { useSopProductionData } from "../../composables/useSopProductionData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
// Which plant actually makes which SKU, in practice -- used only for the optional sanity flag
// below, never to hide/reject data.
const AMBER_ONLY_SKUS = new Set(["M1 Pro"]);
const RONCH_ONLY_SKUS = new Set(["M1-2nd Gen", "M3 Pro"]);
const FACILITY_SPLIT_RELIABLE_FROM = "2026-08-01";

const { dailyRows, snapshotRows, loadError } = useSopProductionData();

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
// else the live (possibly-provisional) figure -- and whether that fallback happened.
function resolvedPlanned(ymd, sku, facility) {
  const snap = (snapshotMap.value[ymd] || {})[sku]?.[facility];
  if (snap !== undefined) return { value: snap, provisional: false };
  const live = (byDateSkuFacility.value[ymd] || {})[sku]?.[facility]?.planned_qty ?? null;
  return { value: live, provisional: true };
}

const combinedTable = computed(() => {
  return dateWindow.value.map(ymd => {
    const perSku = SKUS.map(sku => {
      const planned = resolvedPlanned(ymd, sku, "COMBINED");
      const actual = (byDateSkuFacility.value[ymd] || {})[sku]?.COMBINED?.actual_qty ?? 0;
      return { sku, planned: planned.value, provisional: planned.provisional, actual };
    });
    return {
      ymd,
      plannedTotal: perSku.reduce((s, r) => s + (r.planned || 0), 0),
      actualTotal: perSku.reduce((s, r) => s + (r.actual || 0), 0),
      anyProvisional: perSku.some(r => r.provisional),
      perSku,
    };
  });
});

const facilitySplitTable = computed(() => {
  return dateWindow.value.map(ymd => {
    const perSku = SKUS.map(sku => {
      const ronch = (byDateSkuFacility.value[ymd] || {})[sku]?.RONCH?.actual_qty ?? 0;
      const amber = (byDateSkuFacility.value[ymd] || {})[sku]?.AMBER?.actual_qty ?? 0;
      const combined = (byDateSkuFacility.value[ymd] || {})[sku]?.COMBINED?.actual_qty ?? 0;
      const suspicious = (AMBER_ONLY_SKUS.has(sku) && ronch > 0) || (RONCH_ONLY_SKUS.has(sku) && amber > 0);
      return { sku, ronch, amber, combined, suspicious };
    });
    return { ymd, perSku };
  });
});

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

  <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Planned Production (network-wide)</h3>
  <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
    <table>
      <thead><tr><th>Date</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
      <tbody>
        <tr v-for="r in combinedTable" :key="r.ymd">
          <td>{{ dateLabel(r.ymd) }}
            <span v-if="r.anyProvisional" class="chip chip-open" style="margin-left: 6px;">provisional</span>
          </td>
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
  <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
    <table>
      <thead><tr><th>Date</th><th v-for="s in SKUS" :key="s" class="num">{{ s }}</th><th class="num">Total</th></tr></thead>
      <tbody>
        <tr v-for="r in combinedTable" :key="r.ymd">
          <td>{{ dateLabel(r.ymd) }}</td>
          <td v-for="p in r.perSku" :key="p.sku" class="num mono" :class="{ critical: (p.actual - (p.planned||0)) < 0 }">
            {{ fmt(p.actual - (p.planned || 0)) }}
          </td>
          <td class="num mono"><b>{{ fmt(r.actualTotal - r.plannedTotal) }}</b></td>
        </tr>
      </tbody>
    </table>
  </div></div>

  <h3 style="font-size: 0.95rem; margin: 0 0 10px;">Actual Production by facility -- Ronch / Amber / Combined</h3>
  <p v-if="showPreAugCaveat" class="chip chip-muted" style="display: inline-block; margin-bottom: 12px;">
    Facility split was unmaintained before Aug 2026 -- treat pre-Aug-2026 Ronch/Amber figures as unreliable.
  </p>
  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th rowspan="2">Date</th>
          <th v-for="s in SKUS" :key="s" colspan="3" class="num">{{ s }}</th>
        </tr>
        <tr>
          <template v-for="s in SKUS" :key="s + '-sub'">
            <th class="num">Ronch</th><th class="num">Amber</th><th class="num">Combined</th>
          </template>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in facilitySplitTable" :key="r.ymd">
          <td>{{ dateLabel(r.ymd) }}</td>
          <template v-for="p in r.perSku" :key="p.sku">
            <td class="num mono" :class="{ critical: p.suspicious }">{{ fmt(p.ronch) }}</td>
            <td class="num mono" :class="{ critical: p.suspicious }">{{ fmt(p.amber) }}</td>
            <td class="num mono"><b>{{ fmt(p.combined) }}</b></td>
          </template>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
