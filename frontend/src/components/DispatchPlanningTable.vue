<script setup>
import { reactive, ref, computed, watch } from "vue";
import { supabase } from "../supabaseClient.js";
import { fmtNum, fmtDateOnly, fmtDate } from "../format.js";
import SummaryKpis from "./SummaryKpis.vue";
import BluedartStatusChip from "./BluedartStatusChip.vue";
import DtdcStatusChip from "./DtdcStatusChip.vue";

const props = defineProps({
  rows: { type: Array, required: true },        // mix of kind: "pending" (po_items row, awaiting dispatch) and
                                                  // kind: "shipped" (po_item_shipments row, with .tracking attached)
  filters: { type: Object, required: true },     // reactive filter state, mutated directly (v-model)
  vendorOptions: { type: Array, default: null }, // [{code, label}] -- null hides the Vendor column entirely
  vendorLabel: { type: Function, default: null }, // (code) => string -- required when vendorOptions is set
  onOpenPo: { type: Function, required: true },  // (poCode) => void
  allowConfirmDispatch: { type: Boolean, default: false }, // true from admin.html (Operations/Management/Admin) only
  onDispatched: { type: Function, default: null }, // () => void -- called after a successful confirm, for an instant refresh
});

// "Dispatched" -- confirm_dispatched() logs the shipment (with its AWB and
// courier) then either clears the estimate + queues a fresh vendor
// notification (still pending) or leaves it locked (fully dispatched) --
// see schema.sql. Either way the row that was "pending" drops off (or its
// estimate resets) once poItemsByPo re-fetches (immediately via
// onDispatched, or within the next 60s poll), and the new "shipped" row
// (with its AWB and, shortly after, live courier status) appears in the
// same table automatically once shipmentRows re-fetches too.
const COURIER_OPTIONS = [
  { value: "bluedart", label: "Bluedart" },
  { value: "dtdc", label: "DTDC" },
];
const awbInputs = reactive({});     // "po|sku" -> typed AWB/Tracking ID, mandatory
const courierInputs = reactive({}); // "po|sku" -> selected courier, defaults to Bluedart
const rowErrors = reactive({});  // "po|sku" -> error message
const workingKey = ref(null);

function courierLabel(courier) {
  return COURIER_OPTIONS.find((c) => c.value === courier)?.label || courier || "–";
}

// Seeds a default courier ("Bluedart", by far the more common of the two
// today) for every still-pending row's dropdown as soon as it appears --
// otherwise a plain v-model against an unset reactive key renders with no
// option selected until the user touches the dropdown themselves.
watch(() => props.rows, (rows) => {
  for (const row of rows) {
    if (row.kind === "pending" && courierInputs[keyFor(row)] === undefined) courierInputs[keyFor(row)] = "bluedart";
  }
}, { immediate: true });

function keyFor(row) {
  return row.kind === "shipped" ? `shipped|${row.id}` : `pending|${row.po_code}|${row.item_sku}`;
}

// A dispatch plan is "overdue" once its own promised date has passed
// without Operations having confirmed it -- date-only comparison (a plan
// due today isn't overdue yet). Only meaningful for still-pending rows.
const todayStart = new Date(new Date().toDateString());
function isOverdue(row) {
  return row.kind === "pending" && !!row.estimated_dispatch_date && new Date(row.estimated_dispatch_date) < todayStart;
}

// Buckets a shipped row's courier-specific status_type into one of the
// three KPI tiles below -- each courier has its own status vocabulary
// (Bluedart: short codes; DTDC: free text), so this is the one place that
// needs to know both, rather than spreading courier-specific checks
// across the KPI computation itself.
function trackingBucket(row) {
  const status = row.tracking?.status_type;
  if (row.courier === "bluedart") {
    if (status === "IT") return "in_transit";
    if (status === "DL") return "delivered";
    if (["UD", "RT"].includes(status)) return "exception";
  } else if (row.courier === "dtdc") {
    const s = (status || "").toLowerCase();
    if (["in transit", "out for delivery", "pickup awaited"].includes(s)) return "in_transit";
    if (s === "delivered") return "delivered";
  }
  return null;
}

const kpiTiles = computed(() => {
  const pending = props.rows.filter((r) => r.kind === "pending");
  const shipped = props.rows.filter((r) => r.kind === "shipped");
  const overdue = pending.filter(isOverdue).length;
  const inTransit = shipped.filter((r) => trackingBucket(r) === "in_transit").length;
  const delivered = shipped.filter((r) => trackingBucket(r) === "delivered").length;
  const exceptions = shipped.filter((r) => trackingBucket(r) === "exception").length;
  return [
    { label: "Awaiting dispatch", value: pending.length },
    { label: "Overdue", value: overdue, cls: overdue > 0 ? "critical" : "" },
    { label: "In transit", value: inTransit },
    { label: "Delivered", value: delivered },
    { label: "Exceptions", value: exceptions, cls: exceptions > 0 ? "critical" : "" },
  ];
});

async function handleConfirmDispatch(row) {
  const key = keyFor(row);
  const awb = (awbInputs[key] || "").trim();
  if (!awb) {
    rowErrors[key] = "AWB/Tracking ID is required before confirming dispatch.";
    return;
  }

  workingKey.value = key;
  rowErrors[key] = "";
  const { error } = await supabase.rpc("confirm_dispatched", {
    p_po_code: row.po_code, p_item_sku: row.item_sku, p_awb_number: awb,
    p_courier: courierInputs[key] || "bluedart",
  });
  workingKey.value = null;
  if (error) {
    rowErrors[key] = error.message;
    return;
  }
  delete awbInputs[key];
  delete courierInputs[key];
  if (props.onDispatched) await props.onDispatched();
}
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div class="field" style="max-width: 340px; margin-bottom: 14px;">
    <label for="dispatch-top-search">Search{{ vendorOptions ? " vendor," : "" }} PO code, SKU, AWB…</label>
    <input id="dispatch-top-search" v-model="filters.search" type="text" placeholder="Type to search…">
  </div>

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th v-if="vendorOptions">Vendor</th>
          <th>PO code</th><th>SKU</th><th>Item</th>
          <th class="num">Qty</th><th>Dispatch date</th>
          <th>AWB / Tracking ID</th><th>Courier</th><th>Status</th><th>Route</th><th>Expected delivery</th><th>Last scan</th>
          <th v-if="allowConfirmDispatch"></th>
        </tr>
        <tr class="filter-row">
          <td v-if="vendorOptions">
            <select v-model="filters.vendor">
              <option value="">All</option>
              <option v-for="v in vendorOptions" :key="v.code" :value="v.code">{{ v.label }}</option>
            </select>
          </td>
          <td><input v-model="filters.poCode" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.sku" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.item" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.qty" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.dispatchDate" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.awb" type="text" placeholder="Filter…"></td>
          <td>
            <select v-model="filters.courier">
              <option value="">All</option>
              <option v-for="c in COURIER_OPTIONS" :key="c.value" :value="c.value">{{ c.label }}</option>
            </select>
          </td>
          <td>
            <select v-model="filters.status">
              <option value="">All</option>
              <option value="IT">In transit</option>
              <option value="DL">Delivered</option>
              <option value="UD">Undelivered</option>
              <option value="RT">RTO</option>
              <option value="RL">Redirected</option>
            </select>
          </td>
          <td></td><td></td><td></td>
          <td v-if="allowConfirmDispatch"></td>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!rows.length">
          <td :colspan="(vendorOptions ? 12 : 11) + (allowConfirmDispatch ? 1 : 0)" class="empty-state">Nothing here yet -- rows appear once a vendor fills in an estimated dispatch date and quantity for a SKU, and stay once dispatched with their live shipment status.</td>
        </tr>
        <tr v-for="row in rows" :key="keyFor(row)">
          <td v-if="vendorOptions">{{ vendorLabel(row.vendor_code) }}</td>
          <td class="mono"><button class="link-btn-inline" @click="onOpenPo(row.po_code)">{{ row.po_code }}</button></td>
          <td class="mono">{{ row.item_sku }}</td>
          <td>{{ row.item_name || "–" }}</td>
          <td class="num mono">{{ fmtNum(row.kind === "shipped" ? row.dispatched_qty : row.estimated_dispatch_qty) }}</td>
          <td class="mono">{{ fmtDateOnly(row.kind === "shipped" ? row.dispatched_date : row.estimated_dispatch_date) }}</td>

          <td v-if="row.kind === 'shipped'" class="mono">{{ row.awb_number }}</td>
          <td v-else-if="allowConfirmDispatch">
            <input v-model="awbInputs[keyFor(row)]" type="text" placeholder="Required" style="width: 140px;">
          </td>
          <td v-else class="cell-empty">–</td>

          <td v-if="row.kind === 'shipped'">{{ courierLabel(row.courier) }}</td>
          <td v-else-if="allowConfirmDispatch">
            <select v-model="courierInputs[keyFor(row)]">
              <option v-for="c in COURIER_OPTIONS" :key="c.value" :value="c.value">{{ c.label }}</option>
            </select>
          </td>
          <td v-else class="cell-empty">–</td>

          <td v-if="row.kind === 'shipped' && row.courier === 'bluedart'"><BluedartStatusChip :status-type="row.tracking?.status_type" /></td>
          <td v-else-if="row.kind === 'shipped' && row.courier === 'dtdc'"><DtdcStatusChip :status-type="row.tracking?.status_type" /></td>
          <td v-else-if="row.kind === 'shipped'">{{ row.tracking?.status_text || row.tracking?.status_type || "–" }}</td>
          <td v-else class="cell-empty">Awaiting dispatch</td>

          <td v-if="row.kind === 'shipped'">{{ row.tracking ? `${row.tracking.origin || "–"} → ${row.tracking.destination || "–"}` : "–" }}</td>
          <td v-else class="cell-empty">–</td>

          <td v-if="row.kind === 'shipped'" class="mono">{{ row.tracking?.expected_delivery_date ? fmtDateOnly(row.tracking.expected_delivery_date) : "–" }}</td>
          <td v-else class="cell-empty">–</td>

          <td v-if="row.kind === 'shipped'">
            <template v-if="row.tracking?.last_scan_text">
              {{ row.tracking.last_scan_text }}<br>
              <span class="muted-text">{{ row.tracking.last_scan_location }} · {{ fmtDate(row.tracking.last_scan_at) }}</span>
            </template>
            <template v-else>–</template>
          </td>
          <td v-else class="cell-empty">–</td>

          <td v-if="allowConfirmDispatch">
            <template v-if="row.kind === 'pending'">
              <button class="link-btn-inline" :disabled="workingKey === keyFor(row)" @click="handleConfirmDispatch(row)">
                {{ workingKey === keyFor(row) ? "Working…" : "Dispatched" }}
              </button>
              <div v-if="rowErrors[keyFor(row)]" class="form-error" style="margin: 4px 0 0; font-size: 0.72rem;">{{ rowErrors[keyFor(row)] }}</div>
            </template>
          </td>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
