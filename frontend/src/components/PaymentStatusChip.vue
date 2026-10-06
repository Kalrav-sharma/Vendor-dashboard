<script setup>
import { computed, ref } from "vue";
import { effectivePaymentStatus } from "../format.js";
import { useInvoiceUploads } from "../composables/useInvoiceUploads.js";
import CreditNoteUploadModal from "./CreditNoteUploadModal.vue";
import InvoiceUploadModal from "./InvoiceUploadModal.vue";
import { vendorCtaFor, answerPoCode } from "../composables/useVendorRequests.js";
import downloadIcon from "../assets/icons/download.png";

// row is required (not just status) -- the displayed status now depends on
// reconciliation outcome (match_status) too, once payment_status hasn't
// synced from a payout file yet -- see effectivePaymentStatus() in format.js.
const props = defineProps({
  row: { type: Object, required: true },
  uploaderLabel: { type: String, default: "" }, // current user's display name, recorded on an uploaded credit note
});

const status = computed(() => effectivePaymentStatus(props.row));
const { viewCreditNote } = useInvoiceUploads();
const modalOpen = ref(false);

// A request from the team (vendor side only -- see vendor_request in VendorApp.vue) replaces
// the usual chip with its own call to action, even if a credit note was uploaded earlier.
const request = computed(() => props.row.vendor_request || null);
const invoiceModalOpen = ref(false);
function answerRequest() {
  if (request.value.kind === "credit_note") modalOpen.value = true;
  else invoiceModalOpen.value = true;
}
</script>

<template>
  <div v-if="request" class="vr-cell">
    <button class="link-btn-inline vr-cta" @click.stop="answerRequest">{{ vendorCtaFor(request) }}</button>
    <div v-if="request.note" class="vr-note">“{{ request.note }}”</div>
  </div>

  <div v-else-if="status.wrongPo" class="vr-cell">
    <span class="chip chip-critical" :title="status.title">{{ status.text }}</span>
    <button class="link-btn-inline vr-cta" @click.stop="invoiceModalOpen = true">Upload correct invoice</button>
  </div>

  <span v-else-if="!status.needsCreditNote" class="chip" :class="`chip-${status.cls}`" :title="status.title">{{ status.text }}</span>

  <template v-else-if="row.credit_note_storage_path">
    <span class="chip chip-good" :title="`Uploaded ${row.credit_note_file_name || ''}`">Credit note submitted</span>
    <button class="icon-btn" title="View credit note" aria-label="View credit note" @click.stop="viewCreditNote(row)">
      <img :src="downloadIcon" alt="" class="icon-mono">
    </button>
  </template>

  <button v-else class="link-btn-inline" @click.stop="modalOpen = true">Upload Credit note</button>

  <CreditNoteUploadModal v-if="status.needsCreditNote" v-model="modalOpen" :row="row" :uploader-label="uploaderLabel" />
  <InvoiceUploadModal
    v-if="(request && request.kind !== 'credit_note') || status.wrongPo" v-model="invoiceModalOpen"
    :po-code="request ? answerPoCode(request, row) : row.po_code" :vendor-code="row.vendor_code" :uploader-label="uploaderLabel"
  />
</template>
