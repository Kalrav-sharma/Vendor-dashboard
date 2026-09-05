<script setup>
import { computed, reactive } from "vue";
import StatusChip from "./StatusChip.vue";
import { fmtNum, fmtMoney, TERMINAL_STATUSES } from "../format.js";
import { usePdfDownload } from "../composables/usePdfDownload.js";
import { useInvoiceUploads, validateInvoiceFile } from "../composables/useInvoiceUploads.js";

const props = defineProps({
  rows: { type: Array, required: true },        // already filtered + sorted
  filters: { type: Object, required: true },     // reactive filter state, mutated directly (v-model)
  facilityOptions: { type: Array, required: true },
  statusOptions: { type: Array, required: true },
  vendorOptions: { type: Array, default: null }, // [{code, label}] -- null hides the Vendor column entirely
  grnsByPo: { type: Object, required: true },
  showKpis: { type: Boolean, default: false },   // vendor.html shows KPI cards; admin.html doesn't
  vendorLabel: { type: Function, default: null }, // (code, rowName) => string -- required when vendorOptions is set
  onOpenPo: { type: Function, required: true },
  allowInvoiceUpload: { type: Boolean, default: false }, // lets a row upload without opening the PO detail modal
});

const { downloadingCodes, downloadPoPdf } = usePdfDownload();
const { workingIds, uploadInvoice } = useInvoiceUploads();
const rowUploadErrors = reactive({});

function isUploadingRow(poCode) {
  return workingIds.has(`upload:${poCode}`);
}

async function handleRowInvoiceChosen(e, po) {
  const file = e.target.files?.[0];
  e.target.value = ""; // so choosing the same file again later still fires @change
  if (!file) return;
  rowUploadErrors[po.po_code] = "";

  const validationErr = validateInvoiceFile(file);
  if (validationErr) {
    rowUploadErrors[po.po_code] = validationErr;
    return;
  }

  const result = await uploadInvoice(po.po_code, po.vendor_code, file);
  if (!result.ok) rowUploadErrors[po.po_code] = result.error;
}

function grnInfo(poCode) {
  const grns = props.grnsByPo[poCode] || [];
  const invoices = [...new Set(grns.map(g => g.vendor_invoice_number).filter(Boolean))];
  const recv = grns.reduce((s, g) => s + (Number(g.total_received_amount) || 0), 0);
  const rej = grns.reduce((s, g) => s + (Number(g.total_rejected_amount) || 0), 0);
  return { raised: grns.length > 0, invoiceText: invoices.join(", ") || "–", recv, rej };
}

const kpis = computed(() => {
  if (!props.showKpis) return null;
  const openCount = props.rows.filter(p => !TERMINAL_STATUSES.has(p.status)).length;
  const totalOrdered = props.rows.reduce((s, p) => s + (Number(p.qty_ordered) || 0), 0);
  const totalReceived = props.rows.reduce((s, p) => s + (Number(p.qty_received) || 0), 0);
  const totalAmount = props.rows.reduce((s, p) => s + (Number(p.total_amount) || 0), 0);
  const totalGrnAmount = props.rows.reduce(
    (s, p) => s + (props.grnsByPo[p.po_code] || []).reduce((s2, g) => s2 + (Number(g.total_received_amount) || 0), 0), 0);
  return { total: props.rows.length, openCount, totalOrdered, totalReceived, totalAmount, totalGrnAmount };
});

const statusPills = computed(() => {
  const counts = {};
  for (const p of props.rows) counts[p.status] = (counts[p.status] || 0) + 1;
  return Object.entries(counts).sort((a, b) => b[1] - a[1]);
});
</script>

<template>
  <div v-if="kpis" class="kpis">
    <div class="kpi"><div class="label">Purchase orders</div><div class="value">{{ kpis.total }}</div></div>
    <div class="kpi"><div class="label">Still open</div><div class="value">{{ kpis.openCount }}</div></div>
    <div class="kpi"><div class="label">Units ordered</div><div class="value">{{ fmtNum(kpis.totalOrdered) }}</div></div>
    <div class="kpi"><div class="label">Units received</div><div class="value">{{ fmtNum(kpis.totalReceived) }}</div></div>
    <div class="kpi"><div class="label">PO value</div><div class="value">{{ fmtMoney(kpis.totalAmount) }}</div></div>
    <div class="kpi"><div class="label">GRN value received</div><div class="value">{{ fmtMoney(kpis.totalGrnAmount) }}</div></div>
  </div>

  <div v-if="showKpis" class="stat-pills">
    <div v-for="[status, count] in statusPills" :key="status" class="stat-pill">
      <StatusChip :status="status" /><span class="stat-count">{{ count }}</span>
    </div>
  </div>

  <div class="field" style="max-width: 340px; margin-bottom: 14px;">
    <label for="po-top-search">Search{{ vendorOptions ? " vendor," : "" }} PO code, facility, status, invoice…</label>
    <input id="po-top-search" v-model="filters.search" type="text" placeholder="Type to search…">
  </div>

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th v-if="vendorOptions">Vendor</th>
          <th>Facility</th><th>PO code</th><th>Status</th>
          <th class="num">Qty ordered</th><th class="num">Received</th><th class="num">PO value</th>
          <th>GRN / invoice</th><th>PO Copy</th><th>Invoice Upload</th>
        </tr>
        <tr class="filter-row">
          <td v-if="vendorOptions">
            <select v-model="filters.vendor">
              <option value="">All</option>
              <option v-for="v in vendorOptions" :key="v.code" :value="v.code">{{ v.label }}</option>
            </select>
          </td>
          <td>
            <select v-model="filters.facility">
              <option value="">All</option>
              <option v-for="f in facilityOptions" :key="f" :value="f">{{ f }}</option>
            </select>
          </td>
          <td><input v-model="filters.poCode" type="text" placeholder="Filter…"></td>
          <td>
            <select v-model="filters.status">
              <option value="">All</option>
              <option v-for="s in statusOptions" :key="s" :value="s">{{ s }}</option>
            </select>
          </td>
          <td><input v-model="filters.qtyOrdered" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.qtyReceived" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.poValue" type="text" placeholder="Filter…"></td>
          <td><input v-model="filters.grn" type="text" placeholder="Filter…"></td>
          <td></td>
          <td></td>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!rows.length">
          <td :colspan="vendorOptions ? 10 : 9" class="empty-state">No purchase orders match these filters.</td>
        </tr>
        <tr v-for="p in rows" :key="p.po_code">
          <td v-if="vendorOptions">{{ vendorLabel(p.vendor_code, p.vendor_name) }}</td>
          <td class="fac-code">{{ p.facility }}</td>
          <td class="mono"><button class="link-btn-inline" @click="onOpenPo(p.po_code)">{{ p.po_code }}</button></td>
          <td><StatusChip :status="p.status" /></td>
          <td class="num mono">{{ fmtNum(p.qty_ordered) }}</td>
          <td class="num mono">{{ fmtNum(p.qty_received) }}</td>
          <td class="num mono">{{ fmtMoney(p.total_amount) }}</td>
          <td>
            <span v-if="!grnInfo(p.po_code).raised" class="cell-empty">not yet raised</span>
            <template v-else>
              <div class="invoice-list">{{ grnInfo(p.po_code).invoiceText }}</div>
              <div class="grn-amt">
                {{ fmtMoney(grnInfo(p.po_code).recv) }}
                <span v-if="grnInfo(p.po_code).rej" class="grn-rej">−{{ fmtMoney(grnInfo(p.po_code).rej) }} rej.</span>
              </div>
            </template>
          </td>
          <td>
            <button class="link-btn-inline po-copy-btn" :disabled="downloadingCodes.has(p.po_code)" @click="downloadPoPdf(p.po_code)">
              {{ downloadingCodes.has(p.po_code) ? "Fetching…" : "Download PDF" }}
            </button>
          </td>
          <td>
            <label v-if="allowInvoiceUpload" class="link-btn-inline invoice-upload-btn">
              {{ isUploadingRow(p.po_code) ? "Uploading…" : "+ Add invoice" }}
              <input type="file" accept="application/pdf,.pdf" hidden :disabled="isUploadingRow(p.po_code)" @change="handleRowInvoiceChosen($event, p)">
            </label>
            <div v-if="rowUploadErrors[p.po_code]" class="form-error po-doc-error">{{ rowUploadErrors[p.po_code] }}</div>
          </td>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
