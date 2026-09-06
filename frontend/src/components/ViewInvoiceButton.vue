<script setup>
import { useInvoiceUploads } from "../composables/useInvoiceUploads.js";
import downloadIcon from "../assets/icons/download.png";

const props = defineProps({
  row: { type: Object, required: true }, // po_invoice_uploads row
});

const { viewInvoice } = useInvoiceUploads();
</script>

<template>
  <button class="icon-btn" title="View invoice PDF" aria-label="View invoice PDF" @click.stop="viewInvoice(row)">
    <img :src="downloadIcon" alt="" class="icon-mono">
  </button>
</template>
