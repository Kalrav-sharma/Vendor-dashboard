<script setup>
import { computed } from "vue";
import StatusChip from "./StatusChip.vue";
import InvoiceUploads from "./InvoiceUploads.vue";
import { fmtNum, fmtMoney, fmtDate } from "../format.js";
import { usePdfDownload } from "../composables/usePdfDownload.js";

const props = defineProps({
  po: { type: Object, required: true },
  items: { type: Array, default: () => [] }, // each item pre-augmented with `invoiceText`
  invoices: { type: Array, default: () => [] }, // PO-level invoice numbers
  vendorLabelText: { type: String, default: null }, // null hides the Vendor row (vendor.html)
  allowInvoiceUpload: { type: Boolean, default: false }, // true from both vendor.html and admin.html
});

const { downloadingCodes, downloadPoPdf } = usePdfDownload();
const isDownloading = computed(() => downloadingCodes.has(props.po.po_code));
</script>

<template>
  <div class="meta-grid">
    <div v-if="vendorLabelText"><b>Vendor:</b> {{ vendorLabelText }}</div>
    <div><b>Facility:</b> {{ po.facility }}</div>
    <div><b>Status:</b> <StatusChip :status="po.status" /></div>
    <div><b>Created:</b> {{ fmtDate(po.created_at) }}</div>
    <div><b>PO value:</b> {{ fmtMoney(po.total_amount) }}</div>
    <div><b>Invoice no(s):</b> {{ invoices.length ? invoices.join(", ") : "not yet raised" }}</div>
    <div>
      <button class="link-btn-inline" :disabled="isDownloading" @click="downloadPoPdf(po.po_code)">
        {{ isDownloading ? "Fetching…" : "Download PDF" }}
      </button>
    </div>
  </div>

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead><tr>
        <th>SKU</th><th>Item</th><th class="num">Qty ord</th><th class="num">Recv</th><th class="num">Pending</th>
        <th class="num">Rejected</th><th class="num">Unit price</th><th class="num">Total</th><th>Invoice No(s)</th>
      </tr></thead>
      <tbody>
        <tr v-if="!items.length"><td colspan="9" class="empty-state">No line items on file.</td></tr>
        <tr v-for="item in items" :key="item.item_sku">
          <td class="mono">{{ item.item_sku }}</td>
          <td>{{ item.item_name || "–" }}</td>
          <td class="num mono">{{ fmtNum(item.quantity) }}</td>
          <td class="num mono">{{ fmtNum(item.received_quantity) }}</td>
          <td class="num mono">{{ fmtNum(item.pending_quantity) }}</td>
          <td class="num mono">{{ fmtNum(item.rejected_quantity) }}</td>
          <td class="num mono">{{ fmtMoney(item.unit_price) }}</td>
          <td class="num mono">{{ fmtMoney(item.total) }}</td>
          <td class="mono invoice-list">{{ item.invoiceText }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>

  <InvoiceUploads :po-code="po.po_code" :vendor-code="po.vendor_code" :allow-upload="allowInvoiceUpload" />
</template>
