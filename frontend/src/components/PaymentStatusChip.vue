<script setup>
import { computed } from "vue";
import { paymentStatusLabel, paymentStatusClass } from "../format.js";

// status is null until a payment record syncs for this invoice -- see
// paymentStatusLabel() in format.js for why that reads differently from
// a confirmed "Pending".
const props = defineProps({ status: { type: String, default: null } });
const label = computed(() => paymentStatusLabel(props.status));
const cls = computed(() => paymentStatusClass(props.status));
const title = computed(() => (props.status
  ? undefined
  : "No payment record has synced for this invoice yet."));
</script>

<template>
  <span class="chip" :class="`chip-${cls}`" :title="title">{{ label }}</span>
</template>
