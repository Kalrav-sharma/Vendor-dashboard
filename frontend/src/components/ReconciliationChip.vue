<script setup>
import { computed } from "vue";
import { reconciliationLabel } from "../reconciliation.js";

const props = defineProps({
  row: { type: Object, required: true },
  isInternalStaff: { type: Boolean, default: false }, // see reconciliation.js -- admin.html passes true explicitly
});
const label = computed(() => reconciliationLabel(props.row, props.isInternalStaff));
</script>

<template>
  <span class="chip" :class="`chip-${label.cls}`">{{ label.text }}</span>
</template>
