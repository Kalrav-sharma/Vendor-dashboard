<script setup>
import { fmtNum } from "../format.js";

defineProps({
  rows: { type: Array, required: true },        // already filtered + sorted
  filters: { type: Object, required: true },     // reactive filter state, mutated directly (v-model)
  vendorOptions: { type: Array, default: null }, // [{code, label}] -- null hides the Vendor column entirely
  vendorLabel: { type: Function, default: null }, // (code, rowName) => string -- required when vendorOptions is set
  onOpenSku: { type: Function, required: true }, // (aggKey) => void
});
</script>

<template>
  <div class="field" style="max-width: 340px; margin-bottom: 14px;">
    <label for="sku-top-search">Search{{ vendorOptions ? " vendor," : "" }} SKU, item…</label>
    <input id="sku-top-search" v-model="filters.search" type="text" placeholder="Type to search…">
  </div>

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th v-if="vendorOptions">Vendor</th>
          <th>SKU</th><th>Item</th>
          <th class="num">Qty ordered</th><th class="num">Qty pending</th><th class="num">Qty supplied</th>
          <th class="num">POs pending</th><th class="num">Facilities pending</th>
        </tr>
        <tr class="filter-row">
          <td v-if="vendorOptions">
            <select v-model="filters.vendor">
              <option value="">All</option>
              <option v-for="v in vendorOptions" :key="v.code" :value="v.code">{{ v.label }}</option>
            </select>
          </td>
          <td><input v-model="filters.sku" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.item" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.qtyOrdered" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.qtyPending" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.qtyReceived" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.poCount" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.facilityCount" type="text" placeholder="Filter…"></td>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!rows.length">
          <td :colspan="vendorOptions ? 8 : 7" class="empty-state">No SKUs match these filters.</td>
        </tr>
        <tr v-for="agg in rows" :key="agg.key" class="clickable-row" @click="onOpenSku(agg.key)">
          <td v-if="vendorOptions">{{ vendorLabel(agg.vendor_code, agg.vendor_name) }}</td>
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
