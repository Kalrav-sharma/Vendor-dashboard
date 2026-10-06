<script setup>
// Vendor side: the pop-up shown on login while the team has pending requests on this
// vendor's invoices (credit note / corrected invoice -- see useVendorRequests.js). Each can be
// answered right here; an answered one drops off the list (VendorApp recomputes it from the
// refreshed uploads), and the pop-up closes itself once nothing is left.
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { fmtDateOnly } from "../format.js";
import { REQUEST_KIND_META, vendorCtaFor, answerPoCode } from "../composables/useVendorRequests.js";
import CreditNoteUploadModal from "./CreditNoteUploadModal.vue";
import InvoiceUploadModal from "./InvoiceUploadModal.vue";

const props = defineProps({
  items: { type: Array, required: true }, // VendorApp's pendingRequests: [{ req, upload }]
  uploaderLabel: { type: String, default: "" },
});
const emit = defineEmits(["close", "view-all"]);

const invoiceNumber = (u) => u.match_details?.extracted?.invoice_number || "–";

// The one upload modal open from this list, if any.
const cnRow = ref(null);
const reuploadRow = ref(null);
const childOpen = computed(() => !!(cnRow.value || reuploadRow.value));
function answer(item) {
  const row = { ...item.upload, vendor_request: item.req };
  if (item.req.kind === "credit_note") cnRow.value = row;
  else reuploadRow.value = { ...row, po_code: answerPoCode(item.req, item.upload) };
}

watch([() => props.items.length, childOpen], ([n, child]) => { if (!n && !child) emit("close"); });

function onKeydown(e) { if (e.key === "Escape" && !childOpen.value) emit("close"); }
onMounted(() => document.addEventListener("keydown", onKeydown));
onUnmounted(() => document.removeEventListener("keydown", onKeydown));
</script>

<template>
  <div class="modal-overlay" @click.self="!childOpen && emit('close')">
    <div class="modal-box vr-popup" role="alertdialog" aria-labelledby="vr-popup-title">
      <button class="modal-close-btn" aria-label="Close" @click="emit('close')">&times;</button>
      <div id="vr-popup-title" class="modal-title">
        <span class="vr-popup-dot" aria-hidden="true"></span>
        Action needed on {{ items.length }} invoice{{ items.length === 1 ? "" : "s" }}
      </div>
      <p class="field-hint">Our team has reviewed these invoices against what was received and needs something from you before they can be paid.</p>

      <ul class="vr-popup-list">
        <li v-for="it in items" :key="it.req.id" class="vr-popup-item">
          <div class="vr-popup-head">
            <span class="chip chip-critical">{{ REQUEST_KIND_META[it.req.kind].label }}</span>
            <span class="vr-popup-date">{{ fmtDateOnly(it.req.requested_at) }}</span>
          </div>
          <div class="vr-popup-ref">
            PO <span class="mono">{{ it.upload.po_code }}</span> · Invoice <span class="mono">{{ invoiceNumber(it.upload) }}</span>
          </div>
          <div v-if="it.req.kind === 'dummy_po_invoice'" class="vr-popup-ref">
            Upload the invoice on new PO <span class="mono"><b>{{ it.req.target_po_code }}</b></span>
          </div>
          <div v-if="it.req.note" class="vr-popup-note">“{{ it.req.note }}”</div>
          <button type="button" class="vr-btn vr-popup-cta" @click="answer(it)">{{ vendorCtaFor(it.req) }}</button>
        </li>
      </ul>

      <div class="vr-popup-actions">
        <button type="button" class="link-btn-inline" @click="emit('view-all')">See them on the Payments page</button>
        <button type="button" class="vr-btn" @click="emit('close')">Remind me later</button>
      </div>
    </div>
  </div>

  <CreditNoteUploadModal
    v-if="cnRow" :model-value="true" :row="cnRow" :uploader-label="uploaderLabel"
    @update:model-value="(v) => { if (!v) cnRow = null }"
  />
  <InvoiceUploadModal
    v-if="reuploadRow" :model-value="true" :po-code="reuploadRow.po_code" :vendor-code="reuploadRow.vendor_code"
    :uploader-label="uploaderLabel" @update:model-value="(v) => { if (!v) reuploadRow = null }"
  />
</template>
