<script setup>
import { computed, onMounted, ref } from "vue";
import { useInvoiceUploads } from "../composables/useInvoiceUploads.js";
import { fmtDate } from "../format.js";
import InvoiceUploadModal from "./InvoiceUploadModal.vue";
import InvoiceUploadButton from "./InvoiceUploadButton.vue";
import MatchStatusChip from "./MatchStatusChip.vue";

const props = defineProps({
  poCode: { type: String, required: true },
  vendorCode: { type: String, required: true },
  allowUpload: { type: Boolean, default: false }, // true from both vendor.html and admin.html
  uploaderLabel: { type: String, default: "" }, // current user's display name, recorded on the uploaded row
  expectedInvoiceCount: { type: Number, default: 0 }, // distinct invoice numbers on this PO's GRNs
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
const pendingCount = computed(() => Math.max(0, props.expectedInvoiceCount - rows.value.length));

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
      <InvoiceUploadButton v-if="allowUpload" :on-click="() => uploadModalOpen = true" />
    </div>

    <div v-if="pendingCount > 0" class="chip chip-critical invoice-pending-summary">
      {{ rows.length }} of {{ expectedInvoiceCount }} invoice{{ expectedInvoiceCount === 1 ? "" : "s" }} uploaded --
      {{ pendingCount }} still pending
    </div>

    <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>

    <div v-if="isLoading" class="empty-state">Loading…</div>
    <div v-else-if="!rows.length" class="empty-state">
      {{ allowUpload ? "No invoice copies uploaded yet -- use the button above once you've dispatched against this PO (PDF only)." : "Vendor hasn't uploaded any invoice copies yet." }}
    </div>
    <ul v-else class="invoice-upload-list">
      <li v-for="row in rows" :key="row.id">
        <div class="invoice-upload-row-main">
          <button class="link-btn-inline" @click="viewInvoice(row)">{{ row.file_name }}</button>
          <MatchStatusChip :status="row.match_status" />
          <span class="invoice-upload-meta">
            Uploaded by {{ row.uploaded_by_name || "Unknown" }} ·
            <span class="mono">{{ fmtSize(row.file_size) }} · {{ fmtDate(row.created_at) }}</span>
          </span>
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

    <InvoiceUploadModal
      v-model="uploadModalOpen" :po-code="poCode" :vendor-code="vendorCode" :uploader-label="uploaderLabel"
    />
  </div>
</template>
