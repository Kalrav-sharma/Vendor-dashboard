<script setup>
import { computed } from "vue";
import { reconciliationLabel } from "../reconciliation.js";

const props = defineProps({ row: { type: Object, required: true } });
const label = computed(() => reconciliationLabel(props.row));
</script>

<template>
  <span class="chip" :class="`chip-${label.cls}`">{{ label.text }}</span>
</template>
