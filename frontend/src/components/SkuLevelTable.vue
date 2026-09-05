<script setup>
import { fmtNum } from "../format.js";

defineProps({
  rows: { type: Array, required: true }, // sorted SKU aggregates
  showVendorColumn: { type: Boolean, default: false },
  vendorLabel: { type: Function, default: null }, // (code, rowName) => string -- required when showVendorColumn
  onOpenSku: { type: Function, required: true }, // (aggKey) => void
});
</script>

<template>
  <div class="table-card"><div class="table-scroll">
    <table>
      <thead><tr>
        <th v-if="showVendorColumn">Vendor</th>
        <th>SKU</th><th>Item</th>
        <th class="num">Qty ordered</th><th class="num">Qty pending</th><th class="num">Qty supplied</th>
        <th class="num">POs pending</th><th class="num">Facilities pending</th>
      </tr></thead>
      <tbody>
        <tr v-if="!rows.length">
          <td :colspan="showVendorColumn ? 8 : 7" class="empty-state">No SKUs currently pending — everything's been supplied.</td>
        </tr>
        <tr v-for="agg in rows" :key="agg.key" class="clickable-row" @click="onOpenSku(agg.key)">
          <td v-if="showVendorColumn">{{ vendorLabel(agg.vendor_code, agg.vendor_name) }}</td>
          <td class="mono">{{ agg.item_sku }}</td>
          <td>{{ agg.item_name || "–" }}</td>
          <td class="num mono">{{ fmtNum(agg.qty_ordered) }}</td>
          <td class="num mono">{{ fmtNum(agg.qty_pending) }}</td>
          <td class="num mono">{{ fmtNum(agg.qty_received) }}</td>
          <td class="num mono">{{ agg.poCodes.size }}</td>
          <td class="num mono">{{ agg.facilities.size }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
