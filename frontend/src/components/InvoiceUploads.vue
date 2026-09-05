<script setup>
import { computed, onMounted, ref } from "vue";
import { useInvoiceUploads } from "../composables/useInvoiceUploads.js";
import { fmtDate } from "../format.js";

const props = defineProps({
  poCode: { type: String, required: true },
  vendorCode: { type: String, required: true },
  allowUpload: { type: Boolean, default: false }, // vendor.html only -- admin.html gets a read-only list
});

const { uploadsByPo, loadingPo, workingIds, fetchInvoices, uploadInvoice, deleteInvoice, viewInvoice } = useInvoiceUploads();

const MAX_BYTES = 15 * 1024 * 1024;
const errorMsg = ref("");

const rows = computed(() => uploadsByPo[props.poCode] || []);
const isLoading = computed(() => loadingPo.has(props.poCode));
const isUploading = computed(() => workingIds.has(`upload:${props.poCode}`));

onMounted(() => fetchInvoices(props.poCode));

async function handleFileChosen(e) {
  const file = e.target.files?.[0];
  e.target.value = ""; // so choosing the same file again later still fires @change
  if (!file) return;
  errorMsg.value = "";

  if (file.size > MAX_BYTES) {
    errorMsg.value = `"${file.name}" is larger than 15 MB -- please compress it or split the invoice into parts.`;
    return;
  }

  const result = await uploadInvoice(props.poCode, props.vendorCode, file);
  if (!result.ok) errorMsg.value = result.error;
}

async function handleDelete(row) {
  if (!window.confirm(`Remove "${row.file_name}" from this PO? This can't be undone.`)) return;
  errorMsg.value = "";
  const result = await deleteInvoice(row);
  if (!result.ok) errorMsg.value = result.error;
}

function fmtSize(bytes) {
  if (!bytes) return "";
  return bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
</script>

<template>
  <div class="invoice-uploads">
    <div class="invoice-uploads-head">
      <h4>Invoice copies</h4>
      <label v-if="allowUpload" class="link-btn-inline invoice-upload-btn">
        {{ isUploading ? "Uploading…" : "+ Upload invoice" }}
        <input type="file" accept=".pdf,.jpg,.jpeg,.png,.webp" :disabled="isUploading" hidden @change="handleFileChosen">
      </label>
    </div>

    <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>

    <div v-if="isLoading && !rows.length" class="empty-state">Loading…</div>
    <div v-else-if="!rows.length" class="empty-state">
      {{ allowUpload ? "No invoice copies uploaded yet -- use the button above once you've dispatched against this PO." : "Vendor hasn't uploaded any invoice copies yet." }}
    </div>
    <ul v-else class="invoice-upload-list">
      <li v-for="row in rows" :key="row.id">
        <button class="link-btn-inline" @click="viewInvoice(row)">{{ row.file_name }}</button>
        <span class="invoice-upload-meta mono">{{ fmtSize(row.file_size) }} · {{ fmtDate(row.created_at) }}</span>
        <button class="link-btn-inline invoice-upload-remove" :disabled="workingIds.has(row.id)" @click="handleDelete(row)">
          {{ workingIds.has(row.id) ? "Removing…" : "Remove" }}
        </button>
      </li>
    </ul>
  </div>
</template>
