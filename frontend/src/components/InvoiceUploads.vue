<script setup>
import { computed, onMounted, ref } from "vue";
import { useInvoiceUploads } from "../composables/useInvoiceUploads.js";
import { fmtDate } from "../format.js";
import InvoiceUploadModal from "./InvoiceUploadModal.vue";

const props = defineProps({
  poCode: { type: String, required: true },
  vendorCode: { type: String, required: true },
  allowUpload: { type: Boolean, default: false }, // true from both vendor.html and admin.html
});

const {
  uploadsByPo, loadingPo, workingIds,
  fetchInvoices, deleteInvoice, viewInvoice, checkInvoiceMatch,
} = useInvoiceUploads();

const errorMsg = ref("");
const expandedId = ref(null);
const uploadModalOpen = ref(false);

const rows = computed(() => uploadsByPo[props.poCode] || []);
const isLoading = computed(() => loadingPo.has(props.poCode));

const MATCH_CHIP = {
  pending: ["Checking…", "chip-muted"],
  matched: ["Matches PO/GRN", "chip-good"],
  mismatch: ["Mismatch found", "chip-critical"],
  needs_review: ["Needs review", "chip-open"],
  error: ["Check failed", "chip-critical"],
};
function matchChipLabel(status) { return (MATCH_CHIP[status] || MATCH_CHIP.pending)[0]; }
function matchChipClass(status) { return (MATCH_CHIP[status] || MATCH_CHIP.pending)[1]; }
function isChecking(id) { return workingIds.has(`check:${id}`); }
function toggleDetails(id) { expandedId.value = expandedId.value === id ? null : id; }

onMounted(() => fetchInvoices(props.poCode));

async function handleDelete(row) {
  if (!window.confirm(`Remove "${row.file_name}" from this PO? This can't be undone.`)) return;
  errorMsg.value = "";
  const result = await deleteInvoice(row);
  if (!result.ok) errorMsg.value = result.error;
}

async function handleRecheck(row) {
  errorMsg.value = "";
  const result = await checkInvoiceMatch(props.poCode, row.id);
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
      <button v-if="allowUpload" class="link-btn-inline invoice-upload-btn" @click="uploadModalOpen = true">
        + Upload invoice (PDF)
      </button>
    </div>

    <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>

    <div v-if="isLoading && !rows.length" class="empty-state">Loading…</div>
    <div v-else-if="!rows.length" class="empty-state">
      {{ allowUpload ? "No invoice copies uploaded yet -- use the button above once you've dispatched against this PO (PDF only)." : "Vendor hasn't uploaded any invoice copies yet." }}
    </div>
    <ul v-else class="invoice-upload-list">
      <li v-for="row in rows" :key="row.id">
        <div class="invoice-upload-row-main">
          <button class="link-btn-inline" @click="viewInvoice(row)">{{ row.file_name }}</button>
          <span class="chip" :class="matchChipClass(row.match_status)">{{ matchChipLabel(row.match_status) }}</span>
          <span class="invoice-upload-meta mono">{{ fmtSize(row.file_size) }} · {{ fmtDate(row.created_at) }}</span>
          <button class="link-btn-inline invoice-upload-remove" :disabled="workingIds.has(row.id)" @click="handleDelete(row)">
            {{ workingIds.has(row.id) ? "Removing…" : "Remove" }}
          </button>
        </div>
        <div v-if="row.match_status !== 'pending'" class="invoice-match-row">
          <span>{{ row.match_summary || "Not yet checked." }}</span>
          <button
            v-if="row.match_details?.discrepancies?.length"
            class="link-btn-inline" @click="toggleDetails(row.id)"
          >
            {{ expandedId === row.id ? "Hide details" : "View details" }}
          </button>
          <button class="link-btn-inline" :disabled="isChecking(row.id)" @click="handleRecheck(row)">
            {{ isChecking(row.id) ? "Checking…" : "Re-check" }}
          </button>
        </div>
        <ul v-if="expandedId === row.id && row.match_details?.discrepancies?.length" class="invoice-match-discrepancies">
          <li v-for="(d, i) in row.match_details.discrepancies" :key="i">{{ d.detail }}</li>
        </ul>
      </li>
    </ul>

    <InvoiceUploadModal v-model="uploadModalOpen" :po-code="poCode" :vendor-code="vendorCode" />
  </div>
</template>
