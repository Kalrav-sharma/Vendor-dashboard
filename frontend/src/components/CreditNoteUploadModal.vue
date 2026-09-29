<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { useInvoiceUploads, validateCreditNoteFile } from "../composables/useInvoiceUploads.js";

const props = defineProps({
  modelValue: { type: Boolean, required: true }, // v-model: open/closed
  row: { type: Object, required: true },          // po_invoice_uploads row this credit note corrects
  uploaderLabel: { type: String, default: "" },   // current user's display name, recorded on the row
});
const emit = defineEmits(["update:modelValue"]);

const { uploadCreditNote } = useInvoiceUploads();

const isDragging = ref(false);
const uploading = ref(false);
const errorMsg = ref("");

function close() {
  if (uploading.value) return; // don't let them close out from under an in-flight upload
  emit("update:modelValue", false);
  errorMsg.value = "";
  isDragging.value = false;
}

function onKeydown(e) {
  if (e.key === "Escape") close();
}
onMounted(() => document.addEventListener("keydown", onKeydown));
onUnmounted(() => document.removeEventListener("keydown", onKeydown));

async function handleFile(file) {
  errorMsg.value = "";
  const validationErr = validateCreditNoteFile(file);
  if (validationErr) {
    errorMsg.value = validationErr;
    return;
  }

  uploading.value = true;
  const result = await uploadCreditNote(props.row, file, props.uploaderLabel);
  uploading.value = false;

  if (!result.ok) {
    errorMsg.value = result.error;
    return;
  }
  emit("update:modelValue", false); // success -- close automatically
}

function handleDrop(e) {
  isDragging.value = false;
  const file = e.dataTransfer?.files?.[0];
  if (file) handleFile(file);
}

function handleBrowseChosen(e) {
  const file = e.target.files?.[0];
  e.target.value = ""; // so choosing the same file again later still fires @change
  if (file) handleFile(file);
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="modal-overlay" @click.self="close">
      <div class="modal-box upload-modal-box">
        <button class="modal-close-btn" aria-label="Close" @click="close">&times;</button>
        <div class="modal-title">Upload credit note <span class="mono">(PDF)</span></div>
        <p class="field-hint">
          Reconciliation found a mismatch on invoice
          <span class="mono">{{ row.match_details?.extracted?.invoice_number || row.po_code }}</span> --
          a credit note is needed before this can be paid.
        </p>

        <div
          class="upload-dropzone" :class="{ 'upload-dropzone-active': isDragging }"
          @dragover.prevent="isDragging = true"
          @dragleave.prevent="isDragging = false"
          @drop.prevent="handleDrop"
        >
          <p>Drag and drop the credit note PDF here</p>
          <p class="upload-dropzone-or">or</p>
          <label class="link-btn-inline upload-browse-btn">
            Browse files
            <input type="file" accept="application/pdf,.pdf" hidden :disabled="uploading" @change="handleBrowseChosen">
          </label>
        </div>

        <div v-if="uploading" class="upload-modal-status">Uploading…</div>
        <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
      </div>
    </div>
  </Teleport>
</template>
