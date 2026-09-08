<script setup>
import { fmtNum, fmtDateOnly } from "../format.js";

defineProps({
  rows: { type: Array, required: true },        // already filtered -- one row per po_items row with both estimate fields set
  filters: { type: Object, required: true },     // reactive filter state, mutated directly (v-model)
  vendorOptions: { type: Array, default: null }, // [{code, label}] -- null hides the Vendor column entirely
  vendorLabel: { type: Function, default: null }, // (code) => string -- required when vendorOptions is set
  onOpenPo: { type: Function, required: true },  // (poCode) => void
});
</script>

<template>
  <div class="field" style="max-width: 340px; margin-bottom: 14px;">
    <label for="dispatch-top-search">Search{{ vendorOptions ? " vendor," : "" }} PO code, SKU…</label>
    <input id="dispatch-top-search" v-model="filters.search" type="text" placeholder="Type to search…">
  </div>

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th v-if="vendorOptions">Vendor</th>
          <th>PO code</th><th>SKU</th><th>Item</th>
          <th class="num">Est. dispatch qty</th><th>Est. dispatch date</th>
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
        </tr>
      </thead>
      <tbody>
        <tr v-if="!rows.length">
          <td :colspan="vendorOptions ? 6 : 5" class="empty-state">No dispatch plans yet -- these appear once a vendor fills in an estimated dispatch date and quantity for a SKU.</td>
        </tr>
        <tr v-for="row in rows" :key="row.po_code + '|' + row.item_sku">
          <td v-if="vendorOptions">{{ vendorLabel(row.vendor_code) }}</td>
          <td class="mono"><button class="link-btn-inline" @click="onOpenPo(row.po_code)">{{ row.po_code }}</button></td>
          <td class="mono">{{ row.item_sku }}</td>
          <td>{{ row.item_name || "–" }}</td>
          <td class="num mono">{{ fmtNum(row.estimated_dispatch_qty) }}</td>
          <td class="mono">{{ fmtDateOnly(row.estimated_dispatch_date) }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
