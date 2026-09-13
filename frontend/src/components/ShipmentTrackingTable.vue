<script setup>
import { computed } from "vue";
import { fmtDateOnly, fmtDate } from "../format.js";
import SummaryKpis from "./SummaryKpis.vue";
import BluedartStatusChip from "./BluedartStatusChip.vue";

const props = defineProps({
  rows: { type: Array, required: true },        // po_item_shipments rows, each with an attached `.tracking` (or null)
  filters: { type: Object, required: true },     // reactive filter state, mutated directly (v-model)
  vendorOptions: { type: Array, default: null }, // [{code, label}] -- null hides the Vendor column entirely
  vendorLabel: { type: Function, default: null }, // (code) => string -- required when vendorOptions is set
  onOpenPo: { type: Function, required: true },  // (poCode) => void
});

const kpiTiles = computed(() => {
  const total = props.rows.length;
  const delivered = props.rows.filter((r) => r.tracking?.status_type === "DL").length;
  const inTransit = props.rows.filter((r) => r.tracking?.status_type === "IT").length;
  const exceptions = props.rows.filter((r) => ["UD", "RT"].includes(r.tracking?.status_type)).length;
  return [
    { label: "Shipments", value: total },
    { label: "In transit", value: inTransit },
    { label: "Delivered", value: delivered },
    { label: "Exceptions", value: exceptions, cls: exceptions > 0 ? "critical" : "" },
  ];
});
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div class="field" style="max-width: 340px; margin-bottom: 14px;">
    <label for="shipment-tracking-top-search">Search{{ vendorOptions ? " vendor," : "" }} PO code, SKU, AWB…</label>
    <input id="shipment-tracking-top-search" v-model="filters.search" type="text" placeholder="Type to search…">
  </div>

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th v-if="vendorOptions">Vendor</th>
          <th>PO code</th><th>SKU</th><th>Dispatched</th><th>AWB</th>
          <th>Status</th><th>Route</th><th>Expected delivery</th><th>Last scan</th>
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
          <td></td>
          <td><input v-model="filters.awb" type="text" placeholder="Filter…"></td>
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
        </tr>
      </thead>
      <tbody>
        <tr v-if="!rows.length">
          <td :colspan="vendorOptions ? 9 : 8" class="empty-state">No dispatched shipments yet -- these appear once Operations confirms a dispatch with an AWB/Tracking ID.</td>
        </tr>
        <tr v-for="row in rows" :key="row.id">
          <td v-if="vendorOptions">{{ vendorLabel(row.vendor_code) }}</td>
          <td class="mono"><button class="link-btn-inline" @click="onOpenPo(row.po_code)">{{ row.po_code }}</button></td>
          <td class="mono">{{ row.item_sku }}</td>
          <td class="mono">{{ fmtDateOnly(row.dispatched_date) }}</td>
          <td class="mono">{{ row.awb_number }}</td>
          <td><BluedartStatusChip :status-type="row.tracking?.status_type" /></td>
          <td>{{ row.tracking ? `${row.tracking.origin || "–"} → ${row.tracking.destination || "–"}` : "–" }}</td>
          <td class="mono">{{ row.tracking?.expected_delivery_date ? fmtDateOnly(row.tracking.expected_delivery_date) : "–" }}</td>
          <td>
            <template v-if="row.tracking?.last_scan_text">
              {{ row.tracking.last_scan_text }}<br>
              <span class="muted-text">{{ row.tracking.last_scan_location }} · {{ fmtDate(row.tracking.last_scan_at) }}</span>
            </template>
            <template v-else>–</template>
          </td>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
