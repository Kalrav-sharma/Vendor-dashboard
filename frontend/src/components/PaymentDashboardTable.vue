<script setup>
import { useInvoiceUploads } from "../composables/useInvoiceUploads.js";
import { fmtMoney, fmtDateOnly } from "../format.js";
import MatchStatusChip from "./MatchStatusChip.vue";

defineProps({
  rows: { type: Array, required: true }, // po_invoice_uploads rows
  vendorOptions: { type: Array, default: null }, // [{code, label}] -- null hides the Vendor column entirely
  vendorLabel: { type: Function, default: null }, // (code, rowName) => string -- required when vendorOptions is set
  onOpenPo: { type: Function, required: true }, // (poCode) => void
});

const { viewInvoice } = useInvoiceUploads();

function invoiceValue(row) { return row.match_details?.invoice_value ?? null; }
function grnValue(row) { return row.match_details?.grn_value ?? null; }
function dueDate(row) { return row.match_details?.invoice_due_date || null; }
function dueDateEstimated(row) { return !!row.match_details?.invoice_due_date_estimated; }
</script>

<template>
  <div class="table-card"><div class="table-scroll">
    <table>
      <thead><tr>
        <th v-if="vendorOptions">Vendor</th>
        <th>PO code</th><th>Invoice file</th>
        <th class="num">Invoice value</th><th class="num">GRN value</th>
        <th>Due date</th><th>Reconciliation</th><th>Payment status</th>
      </tr></thead>
      <tbody>
        <tr v-if="!rows.length">
          <td :colspan="vendorOptions ? 8 : 7" class="empty-state">No invoices uploaded yet.</td>
        </tr>
        <tr v-for="row in rows" :key="row.id">
          <td v-if="vendorOptions">{{ vendorLabel(row.vendor_code) }}</td>
          <td class="mono"><button class="link-btn-inline" @click="onOpenPo(row.po_code)">{{ row.po_code }}</button></td>
          <td><button class="link-btn-inline" @click="viewInvoice(row)">{{ row.file_name }}</button></td>
          <td class="num mono">{{ fmtMoney(invoiceValue(row)) }}</td>
          <td class="num mono">{{ fmtMoney(grnValue(row)) }}</td>
          <td class="mono">
            {{ fmtDateOnly(dueDate(row)) }}
            <span
              v-if="dueDateEstimated(row)" class="due-date-estimated-mark"
              title="Not printed on the invoice -- estimated as 45 days from the invoice date."
            >*</span>
          </td>
          <td><MatchStatusChip :status="row.match_status" /></td>
          <td>
            <span class="chip chip-muted" title="Payment status will sync automatically once Oracle integration is built.">
              Pending integration
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div></div>
  <p v-if="rows.some(dueDateEstimated)" class="field-hint">* not printed on the invoice -- estimated as 45 days from the invoice date.</p>
</template>
