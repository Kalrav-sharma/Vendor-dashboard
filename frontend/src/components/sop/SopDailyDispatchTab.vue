<script setup>
// S&OP > Daily Dispatch Planner (2026-09-17, per Anish) -- the first-mile truck plan from the two
// production plants (Ronch, Pune / Amber, Gurgaon) to the 5 UC warehouses, rendered from the same
// engine the /first-mile-dispatch-decision skill runs. Verified to produce byte-identical plans, so
// this tab can be trusted without re-running the skill.
//
// THE TOGGLE: the skill asks, on every run, whether today's production should count as dispatchable.
// It's only safe to add on top of the plant FG snapshot if that snapshot predates today's finished
// output (a morning run); counting it again in the evening double-counts it. There's no timestamp on
// the FG table to decide automatically, which is why it's a question rather than a computation. The
// portal computes BOTH and toggles, which is strictly more useful than the prompt -- you can see
// what today's run actually buys you. Default is EXCLUDE: plan from what's provably on the floor.
import { computed, ref } from "vue";
import { useSopFirstMileData } from "../../composables/useSopFirstMileData.js";
import SummaryKpis from "../SummaryKpis.vue";

const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const FACILITIES = ["RONCH", "AMBER"];
const SCENARIOS = [
  { key: "EXCLUDE_TODAY", label: "Excl. today's production", sub: "FG on the floor now" },
  { key: "INCLUDE_TODAY", label: "Incl. today's production", sub: "assumes today's run lands" },
];

const { planRows, plantRows, fillRows, utilRows, missedRows, runDate, loadError } = useSopFirstMileData();
const activeScenario = ref(SCENARIOS[0].key);

function fmt(n) {
  return Math.round(n || 0).toLocaleString("en-IN");
}
function fmtDate(ymd) {
  if (!ymd) return "";
  const d = new Date(`${ymd}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? ymd
    : d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

const forScenario = (rows, key) => rows.value.filter(r => r.scenario === key);

// One row per truck, rebuilt from the per-SKU plan rows (the table stores a line per truck x SKU so
// the SKU mix is queryable, but a truck is what actually departs).
function trucksFor(key) {
  const byTruck = new Map();
  for (const r of forScenario(planRows, key)) {
    const id = `${r.dispatch_date}|${r.facility}|${r.warehouse}|${r.eta}|${r.truck_total}`;
    if (!byTruck.has(id)) {
      byTruck.set(id, {
        id, dispatch_date: r.dispatch_date, facility: r.facility, warehouse: r.warehouse,
        eta: r.eta, total: r.truck_total, reason: r.reason, tier: r.tier, skus: [],
      });
    }
    byTruck.get(id).skus.push(`${r.sku}:${fmt(r.qty)}`);
  }
  return [...byTruck.values()].sort(
    (a, b) => a.dispatch_date.localeCompare(b.dispatch_date) || a.facility.localeCompare(b.facility)
  );
}

const trucks = computed(() => trucksFor(activeScenario.value));
const plant = computed(() => forScenario(plantRows, activeScenario.value));
const fill = computed(() => forScenario(fillRows, activeScenario.value));
const missed = computed(() => forScenario(missedRows, activeScenario.value));

const missedTotals = computed(() => ({
  pos: missed.value.length,
  reschedule: missed.value.filter(r => r.status === "RESCHEDULE").length,
  short: missed.value.reduce((s, r) => s + Number(r.short || 0), 0),
}));

const fillTotals = computed(() => {
  const rows = fill.value;
  const ordered = rows.reduce((s, r) => s + Number(r.ordered || 0), 0);
  const served = rows.reduce((s, r) => s + Number(r.served || 0), 0);
  const short = rows.reduce((s, r) => s + Number(r.short || 0), 0);
  const fixable = rows.reduce((s, r) => s + Number(r.dispatch_fixable || 0), 0);
  return { ordered, served, short, fixable, pct: ordered > 0 ? (served / ordered) * 100 : null };
});

const plantTotal = (facility, field) =>
  plant.value.filter(r => r.facility === facility).reduce((s, r) => s + Number(r[field] || 0), 0);

const kpiTiles = computed(() => [
  { label: "Ronch FG", value: fmt(plantTotal("RONCH", "fg_qty")) },
  { label: "Amber FG", value: fmt(plantTotal("AMBER", "fg_qty")) },
  { label: "Today's Production", value: fmt(plantTotal("RONCH", "production_raw") + plantTotal("AMBER", "production_raw")), cls: "info" },
  { label: "Trucks", value: fmt(trucks.value.length) },
  { label: "Units Dispatched", value: fmt(trucks.value.reduce((s, t) => s + Number(t.total || 0), 0)) },
  {
    label: "PO Fill",
    value: fillTotals.value.pct == null ? "–" : `${fillTotals.value.pct.toFixed(1)}%`,
    cls: fillTotals.value.pct != null && fillTotals.value.pct < 95 ? "critical" : "good",
  },
]);

// What flipping the toggle actually changes -- otherwise you'd have to eyeball two tables.
const scenarioDelta = computed(() => {
  const a = trucksFor("EXCLUDE_TODAY");
  const b = trucksFor("INCLUDE_TODAY");
  const units = x => x.reduce((s, t) => s + Number(t.total || 0), 0);
  const fillPct = (key) => {
    const rows = forScenario(fillRows, key);
    const o = rows.reduce((s, r) => s + Number(r.ordered || 0), 0);
    const s2 = rows.reduce((s, r) => s + Number(r.served || 0), 0);
    return o > 0 ? (s2 / o) * 100 : null;
  };
  const pa = fillPct("EXCLUDE_TODAY"), pb = fillPct("INCLUDE_TODAY");
  if (!a.length && !b.length) return null;
  return {
    trucks: b.length - a.length,
    units: units(b) - units(a),
    fill: pa != null && pb != null ? pb - pa : null,
  };
});

const signed = (n, suffix = "") => `${n > 0 ? "+" : ""}${fmt(n)}${suffix}`;

// Tier 0 means a committed PO goes unserved without this truck -- the only tier that is a promise
// already made to a customer, so it gets the strongest tint. reason collapses tiers 0 and 1 into
// one word; this keeps them apart.
function truckClass(t) {
  if (t.tier === 0) return "cell-critical";
  if (t.reason === "EMERGENCY") return "cell-open";
  return "";
}
</script>

<template>
  <div v-if="loadError" class="form-error">{{ loadError }}</div>

  <div v-if="!runDate && !loadError" class="panel" style="margin-bottom: 18px;">
    <p class="muted-text" style="margin: 0;">
      No dispatch plan has been synced yet. The first run of
      <b>Sync S&amp;OP first-mile dispatch plan</b> will populate this tab.
    </p>
  </div>

  <template v-if="runDate">
    <SummaryKpis :tiles="kpiTiles" />

    <div class="panel" style="margin-bottom: 14px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
      <button
        v-for="s in SCENARIOS" :key="s.key"
        class="toggle-btn" :class="{ active: activeScenario === s.key }"
        @click="activeScenario = s.key"
      >
        <b>{{ s.label }}</b> <span class="sub">({{ s.sub }})</span>
      </button>
      <span v-if="scenarioDelta" class="muted-text" style="margin-left: auto;">
        Counting today's production changes the plan by
        <b>{{ signed(scenarioDelta.trucks) }}</b> truck(s),
        <b>{{ signed(scenarioDelta.units) }}</b> units<template v-if="scenarioDelta.fill !== null">,
        <b>{{ scenarioDelta.fill > 0 ? "+" : "" }}{{ scenarioDelta.fill.toFixed(1) }}pp</b> PO fill</template>.
      </span>
    </div>

    <h3 class="section-title">At the plants</h3>
    <p class="muted-text" style="margin: -8px 0 12px;">
      Plan as of {{ fmtDate(runDate) }}. <b>FG</b> is dispatchable finished goods.
      <b>Hold</b> is real stock that is blocked (QC, quarantine, allocation) and is deliberately
      excluded from the plan. Today's production is shown as made; the engine applies a 10% yield
      factor before dispatching it.
    </p>
    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Plant</th><th></th>
            <th v-for="s in SKUS" :key="s" class="num">{{ s }}</th>
            <th class="num">Total</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="f in FACILITIES" :key="f">
            <tr>
              <td :rowspan="3"><b>{{ f }}</b></td>
              <td>FG <span class="chip chip-good">ready</span></td>
              <td v-for="s in SKUS" :key="s" class="num mono">
                {{ fmt(plant.find(r => r.facility === f && r.sku === s)?.fg_qty) }}
              </td>
              <td class="num mono"><b>{{ fmt(plantTotal(f, "fg_qty")) }}</b></td>
            </tr>
            <tr>
              <td>Hold <span class="chip chip-muted">blocked</span></td>
              <td v-for="s in SKUS" :key="s" class="num mono muted-text">
                {{ fmt(plant.find(r => r.facility === f && r.sku === s)?.hold_qty) }}
              </td>
              <td class="num mono muted-text">{{ fmt(plantTotal(f, "hold_qty")) }}</td>
            </tr>
            <tr class="row-total">
              <td>Today's production</td>
              <td v-for="s in SKUS" :key="s" class="num mono">
                {{ fmt(plant.find(r => r.facility === f && r.sku === s)?.production_raw) }}
              </td>
              <td class="num mono">{{ fmt(plantTotal(f, "production_raw")) }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div></div>

    <h3 class="section-title">Dispatch plan</h3>
    <p class="muted-text" style="margin: -8px 0 12px;">
      {{ trucks.length }} truck(s), each 500&ndash;725 units. Red rows carry a committed PO that goes
      unserved without them; amber rows are replenishing a warehouse below its cover floor.
    </p>
    <div class="table-card" style="margin-bottom: 24px;"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Dispatch</th><th>Plant</th><th>&rarr; Warehouse</th><th>SKU breakdown</th>
            <th class="num">Total</th><th>ETA</th><th>Reason</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in trucks" :key="t.id">
            <td>{{ fmtDate(t.dispatch_date) }}</td>
            <td>{{ t.facility }}</td>
            <td><b>{{ t.warehouse }}</b></td>
            <td class="mono" style="font-size: 0.8rem;">{{ t.skus.join(", ") }}</td>
            <td class="num mono"><b>{{ fmt(t.total) }}</b></td>
            <td>{{ fmtDate(t.eta) }}</td>
            <td :class="truckClass(t)">{{ t.reason }}</td>
          </tr>
          <tr v-if="!trucks.length"><td colspan="7" class="muted-text">No dispatches planned.</td></tr>
        </tbody>
      </table>
    </div></div>

    <h3 class="section-title">POs at risk</h3>
    <p class="muted-text" style="margin: -8px 0 12px;">
      Committed channel orders this plan does not fully cover.
      <b>PARTIAL</b> means the trucks bring some of it; <b>RESCHEDULE</b> means they bring none.
      Judged against <i>this</i> dispatch plan, so a PO the trucks rescue drops off the list &mdash;
      which is why this can differ from the PO Fulfillment tab, where the question is what the
      warehouse can serve from its own stock.
    </p>
    <div class="table-card"><div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Date</th><th>PO Number</th><th>Warehouse</th><th>Channel</th><th>SKU</th>
            <th class="num">Ordered</th><th class="num">Served</th><th class="num">Short</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in missed" :key="r.id">
            <td>{{ fmtDate(r.po_date) }}</td>
            <td class="mono" style="font-size: 0.8rem;">{{ r.po_number || "–" }}</td>
            <td>{{ r.warehouse }}</td>
            <td>{{ r.channel }}</td>
            <td>{{ r.sku }}</td>
            <td class="num mono">{{ fmt(r.ordered) }}</td>
            <td class="num mono">{{ fmt(r.served) }}</td>
            <td class="num mono"><b>{{ fmt(r.short) }}</b></td>
            <td>
              <span class="chip" :class="r.status === 'RESCHEDULE' ? 'chip-critical' : 'chip-open'">
                {{ r.status }}
              </span>
            </td>
          </tr>
          <tr v-if="!missed.length">
            <td colspan="9" class="muted-text">
              Every committed order is served in full and on time.
            </td>
          </tr>
        </tbody>
      </table>
    </div></div>
    <p v-if="missed.length" class="muted-text" style="margin: 10px 0 0;">
      <b>{{ missedTotals.pos }}</b> PO(s) at risk &mdash; {{ missedTotals.reschedule }} needing a
      reschedule &mdash; <b>{{ fmt(missedTotals.short) }}</b> units short of
      {{ fmt(fillTotals.ordered) }} ordered.
      Where a PO shares a day, warehouse and SKU with others, the shortfall is attributed in sheet
      order: earlier orders are filled first.
    </p>
  </template>
</template>
