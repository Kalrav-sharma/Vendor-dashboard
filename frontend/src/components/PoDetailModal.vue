<script setup>
import { reactive } from "vue";
import { supabase } from "../supabaseClient.js";
import StatusChip from "./StatusChip.vue";
import InvoiceUploads from "./InvoiceUploads.vue";
import DownloadPdfButton from "./DownloadPdfButton.vue";
import { fmtNum, fmtMoney, fmtDate } from "../format.js";

const props = defineProps({
  po: { type: Object, required: true },
  items: { type: Array, default: () => [] }, // each item pre-augmented with `invoiceText`
  invoices: { type: Array, default: () => [] }, // PO-level invoice numbers
  vendorLabelText: { type: String, default: null }, // null hides the Vendor row (vendor.html)
  allowInvoiceUpload: { type: Boolean, default: false }, // true from both vendor.html and admin.html
  uploaderLabel: { type: String, default: "" }, // current user's display name, recorded on an uploaded row
  allowDispatchPlanning: { type: Boolean, default: false }, // true from both vendor.html and admin.html
});

// Local per-SKU edit state for the two Dispatch Planning fields -- `items`
// itself is a fresh mapped array (see AdminApp.vue/VendorApp.vue's
// openPoDetailModal), so editing it directly wouldn't persist; this saves
// straight to Supabase via a plain RLS-gated update (see schema.sql's
// po_items_update_dispatch policy + column-level GRANT), no Edge Function
// needed since it's just the calling user's own permitted columns/rows.
const editState = reactive({}); // item_sku -> { date, qty, saving, error }

function stateFor(item) {
  if (!editState[item.item_sku]) {
    editState[item.item_sku] = {
      date: item.estimated_dispatch_date || "",
      qty: item.estimated_dispatch_qty ?? "",
      saving: false,
      error: "",
    };
  }
  return editState[item.item_sku];
}

async function saveDispatchInfo(item) {
  const st = stateFor(item);
  st.saving = true;
  st.error = "";
  const { error } = await supabase
    .from("po_items")
    .update({
      estimated_dispatch_date: st.date || null,
      estimated_dispatch_qty: st.qty === "" ? null : Number(st.qty),
    })
    .eq("po_code", props.po.po_code)
    .eq("item_sku", item.item_sku);
  st.saving = false;
  if (error) {
    st.error = error.message;
    return;
  }
  item.estimated_dispatch_date = st.date || null;
  item.estimated_dispatch_qty = st.qty === "" ? null : Number(st.qty);
}
</script>

<template>
  <div class="meta-grid">
    <div v-if="vendorLabelText"><b>Vendor:</b> {{ vendorLabelText }}</div>
    <div><b>Facility:</b> {{ po.facility }}</div>
    <div><b>Status:</b> <StatusChip :status="po.status" /></div>
    <div><b>Created:</b> {{ fmtDate(po.created_at) }}</div>
    <div><b>PO value:</b> {{ fmtMoney(po.total_amount) }}</div>
    <div><b>Invoice no(s):</b> {{ invoices.length ? invoices.join(", ") : "not yet raised" }}</div>
    <div><b>PO copy:</b> <DownloadPdfButton :po-code="po.po_code" /></div>
  </div>

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead><tr>
        <th>SKU</th><th>Item</th><th class="num">Qty ord</th><th class="num">Recv</th><th class="num">Pending</th>
        <th class="num">Rejected</th><th class="num">Unit price</th><th class="num">Total</th><th>Invoice No(s)</th>
        <th>Est. dispatch date</th><th class="num">Est. dispatch qty</th><th v-if="allowDispatchPlanning"></th>
      </tr></thead>
      <tbody>
        <tr v-if="!items.length"><td :colspan="allowDispatchPlanning ? 12 : 11" class="empty-state">No line items on file.</td></tr>
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
          <td>
            <input v-if="allowDispatchPlanning" v-model="stateFor(item).date" type="date" style="width: 140px;">
            <span v-else class="mono">{{ item.estimated_dispatch_date ? fmtDate(item.estimated_dispatch_date) : "–" }}</span>
          </td>
          <td class="num">
            <input v-if="allowDispatchPlanning" v-model="stateFor(item).qty" type="number" min="0" style="width: 80px;">
            <span v-else class="mono">{{ fmtNum(item.estimated_dispatch_qty) }}</span>
          </td>
          <td v-if="allowDispatchPlanning">
            <button class="link-btn-inline" :disabled="stateFor(item).saving" @click="saveDispatchInfo(item)">
              {{ stateFor(item).saving ? "Saving…" : "Save" }}
            </button>
            <div v-if="stateFor(item).error" class="form-error" style="margin: 4px 0 0; font-size: 0.72rem;">{{ stateFor(item).error }}</div>
          </td>
        </tr>
      </tbody>
    </table>
  </div></div>

  <InvoiceUploads
    :po-code="po.po_code" :vendor-code="po.vendor_code"
    :allow-upload="allowInvoiceUpload" :uploader-label="uploaderLabel"
    :expected-invoice-count="invoices.length"
  />
</template>
