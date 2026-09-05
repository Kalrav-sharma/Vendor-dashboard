<script setup>
import { computed } from "vue";
import StatusChip from "./StatusChip.vue";
import { fmtNum, fmtMoney } from "../format.js";

const props = defineProps({
  agg: { type: Object, required: true },
  vendorLabelText: { type: String, default: null }, // null hides the vendor scope line (vendor.html)
  onOpenPo: { type: Function, required: true }, // (poCode) => void, opens the PO detail modal
});

const sortedDetails = computed(() =>
  [...props.agg.details].sort((a, b) => (Number(b.qty_pending) || 0) - (Number(a.qty_pending) || 0))
);
</script>

<template>
  <div v-if="vendorLabelText" class="scope" style="margin-bottom: 12px;">Vendor: {{ vendorLabelText }}</div>
  <div class="table-card"><div class="table-scroll">
    <table>
      <thead><tr>
        <th>PO code</th><th>Facility</th><th>Status</th>
        <th class="num">Qty ordered</th><th class="num">Qty pending</th><th class="num">Qty received</th><th class="num">Unit price</th>
      </tr></thead>
      <tbody>
        <tr v-for="d in sortedDetails" :key="d.po_code">
          <td class="mono"><button class="link-btn-inline" @click="onOpenPo(d.po_code)">{{ d.po_code }}</button></td>
          <td class="fac-code">{{ d.facility }}</td>
          <td><StatusChip :status="d.status" /></td>
          <td class="num mono">{{ fmtNum(d.qty_ordered) }}</td>
          <td class="num mono">{{ fmtNum(d.qty_pending) }}</td>
          <td class="num mono">{{ fmtNum(d.qty_received) }}</td>
          <td class="num mono">{{ fmtMoney(d.unit_price) }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
