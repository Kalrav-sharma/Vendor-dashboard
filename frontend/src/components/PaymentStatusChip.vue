<script setup>
import { computed, ref } from "vue";
import { effectivePaymentStatus } from "../format.js";
import { useInvoiceUploads } from "../composables/useInvoiceUploads.js";
import CreditNoteUploadModal from "./CreditNoteUploadModal.vue";
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
</script>

<template>
  <span v-if="!status.needsCreditNote" class="chip" :class="`chip-${status.cls}`" :title="status.title">{{ status.text }}</span>

  <template v-else-if="row.credit_note_storage_path">
    <span class="chip chip-good" :title="`Uploaded ${row.credit_note_file_name || ''}`">Credit note submitted</span>
    <button class="icon-btn" title="View credit note" aria-label="View credit note" @click.stop="viewCreditNote(row)">
      <img :src="downloadIcon" alt="" class="icon-mono">
    </button>
  </template>

  <button v-else class="link-btn-inline" @click.stop="modalOpen = true">Upload Credit note</button>

  <CreditNoteUploadModal v-if="status.needsCreditNote" v-model="modalOpen" :row="row" :uploader-label="uploaderLabel" />
</template>
