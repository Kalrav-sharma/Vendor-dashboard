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
//
// Changing an ALREADY-SET value (not the first entry) requires an explicit
// confirmation + a mandatory reason (Kalrav's spec) -- pendingConfirm/
// confirmChecked/reason below drive that inline panel; a first-time entry
// (both fields currently null) saves immediately, no confirmation needed.
const editState = reactive({}); // item_sku -> { date, qty, saving, error, pendingConfirm, confirmChecked, reason }

function stateFor(item) {
  if (!editState[item.item_sku]) {
    editState[item.item_sku] = {
      date: item.estimated_dispatch_date || "",
      qty: item.estimated_dispatch_qty ?? "",
      saving: false,
      error: "",
      pendingConfirm: false,
      confirmChecked: false,
      reason: "",
    };
  }
  return editState[item.item_sku];
}

// Once a SKU has nothing left pending (per Uniware's own synced figure --
// not something this feature tracks itself), there's nothing further to
// dispatch, so its estimate fields lock: no more edits, no more resets.
// Kept as a plain function (not computed) since it reads a plain prop
// field per-item, same reasoning as stateFor() above.
function isLocked(item) {
  return (item.pending_quantity ?? 0) <= 0;
}

function handleSaveClick(item) {
  if (isLocked(item)) return; // Save button is hidden when locked; guard anyway
  const st = stateFor(item);
  const newDate = st.date || null;
  const newQty = st.qty === "" ? null : Number(st.qty);
  const hadExisting = item.estimated_dispatch_date != null || item.estimated_dispatch_qty != null;
  const changed = newDate !== (item.estimated_dispatch_date || null) || newQty !== (item.estimated_dispatch_qty ?? null);

  if (hadExisting && changed) {
    st.pendingConfirm = true; // show the inline confirm-and-reason panel instead of saving yet
    return;
  }
  doSave(item, newDate, newQty);
}

function cancelChange(item) {
  const st = stateFor(item);
  st.pendingConfirm = false;
  st.confirmChecked = false;
  st.reason = "";
  st.date = item.estimated_dispatch_date || ""; // revert the unsaved edit
  st.qty = item.estimated_dispatch_qty ?? "";
}

function confirmChange(item) {
  const st = stateFor(item);
  if (!st.confirmChecked || !st.reason.trim()) return; // guard -- button is disabled for this already
  doSave(item, st.date || null, st.qty === "" ? null : Number(st.qty), {
    oldDate: item.estimated_dispatch_date,
    oldQty: item.estimated_dispatch_qty,
    reason: st.reason.trim(),
  });
}

async function doSave(item, newDate, newQty, changeAudit) {
  const st = stateFor(item);
  st.saving = true;
  st.error = "";
  const { error } = await supabase
    .from("po_items")
    .update({ estimated_dispatch_date: newDate, estimated_dispatch_qty: newQty })
    .eq("po_code", props.po.po_code)
    .eq("item_sku", item.item_sku);
  if (error) {
    st.saving = false;
    st.error = error.message;
    return;
  }

  if (changeAudit) {
    await supabase.from("po_item_dispatch_changes").insert({
      po_code: props.po.po_code, item_sku: item.item_sku, vendor_code: props.po.vendor_code,
      changed_by: props.uploaderLabel || null,
      old_estimated_dispatch_date: changeAudit.oldDate, old_estimated_dispatch_qty: changeAudit.oldQty,
      new_estimated_dispatch_date: newDate, new_estimated_dispatch_qty: newQty,
      reason: changeAudit.reason,
    });
  }

  item.estimated_dispatch_date = newDate;
  item.estimated_dispatch_qty = newQty;
  st.saving = false;
  st.pendingConfirm = false;
  st.confirmChecked = false;
  st.reason = "";
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
        <template v-for="item in items" :key="item.item_sku">
        <tr>
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
            <input v-if="allowDispatchPlanning && !isLocked(item)" v-model="stateFor(item).date" type="date" style="width: 140px;">
            <span v-else class="mono">{{ item.estimated_dispatch_date ? fmtDate(item.estimated_dispatch_date) : "–" }}</span>
          </td>
          <td class="num">
            <input v-if="allowDispatchPlanning && !isLocked(item)" v-model="stateFor(item).qty" type="number" min="0" style="width: 80px;">
            <span v-else class="mono">{{ fmtNum(item.estimated_dispatch_qty) }}</span>
          </td>
          <td v-if="allowDispatchPlanning">
            <template v-if="!isLocked(item)">
              <button class="link-btn-inline" :disabled="stateFor(item).saving" @click="handleSaveClick(item)">
                {{ stateFor(item).saving ? "Saving…" : "Save" }}
              </button>
              <div v-if="stateFor(item).error" class="form-error" style="margin: 4px 0 0; font-size: 0.72rem;">{{ stateFor(item).error }}</div>
            </template>
            <span v-else class="chip chip-good" title="Nothing left pending on this SKU -- no further dispatch needed.">Fully dispatched</span>
          </td>
        </tr>
        <tr v-if="allowDispatchPlanning && !isLocked(item) && stateFor(item).pendingConfirm">
          <td :colspan="12" style="background: var(--paper);">
            <div style="padding: 10px 4px;">
              <p style="margin: 0 0 8px; font-size: 0.85rem;">
                <b>{{ item.item_sku }}</b> already has an estimated dispatch date/quantity on file -- are you sure you want to change it?
              </p>
              <label style="display: flex; align-items: center; gap: 8px; font-size: 0.85rem; margin-bottom: 8px;">
                <input v-model="stateFor(item).confirmChecked" type="checkbox">
                I confirm I want to change the already-entered information for this SKU.
              </label>
              <div class="field" style="margin-bottom: 8px;">
                <label>Reason for change (required)</label>
                <input v-model="stateFor(item).reason" type="text" placeholder="e.g. Vendor factory delay, revised production schedule">
              </div>
              <div style="display: flex; gap: 12px; align-items: center;">
                <button
                  class="primary-btn" style="width: auto; padding: 7px 16px;"
                  :disabled="!stateFor(item).confirmChecked || !stateFor(item).reason.trim() || stateFor(item).saving"
                  @click="confirmChange(item)"
                >
                  {{ stateFor(item).saving ? "Saving…" : "Confirm change" }}
                </button>
                <button class="link-btn-inline" @click="cancelChange(item)">Cancel</button>
              </div>
            </div>
          </td>
        </tr>
        </template>
      </tbody>
    </table>
  </div></div>

  <InvoiceUploads
    :po-code="po.po_code" :vendor-code="po.vendor_code"
    :allow-upload="allowInvoiceUpload" :uploader-label="uploaderLabel"
    :expected-invoice-count="invoices.length"
  />
</template>
