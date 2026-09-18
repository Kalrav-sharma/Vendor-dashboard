<script setup>
import { computed } from "vue";
import { invoiceStatusLabel, invoiceStatusClass } from "../format.js";

// status is po_invoice_uploads.oracle_status -- whether the invoice record
// reached Oracle, NOT whether it was paid. See invoiceStatusLabel() in
// format.js before repurposing this as a payment indicator.
const props = defineProps({ status: { type: String, default: null } });
const label = computed(() => invoiceStatusLabel(props.status));
const cls = computed(() => invoiceStatusClass(props.status));

const TITLES = {
  pushed: "This invoice has been submitted to Oracle, UC's payables system. This is not confirmation of payment.",
  not_attempted: "This invoice hasn't been submitted to Oracle yet.",
  failed: "Submitting this invoice to Oracle failed — it needs attention before it can be paid.",
};
const title = computed(() => TITLES[props.status]
  || "No submission record has synced for this invoice yet.");
</script>

<template>
  <span class="chip" :class="`chip-${cls}`" :title="title">{{ label }}</span>
</template>
