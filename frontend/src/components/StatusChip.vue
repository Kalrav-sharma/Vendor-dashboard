<script setup>
import { computed } from "vue";
import { statusLabel, statusClass } from "../format.js";

const props = defineProps({ status: { type: String, default: null } });
const label = computed(() => statusLabel(props.status));
const cls = computed(() => statusClass(props.status));
</script>

<template>
  <span class="chip" :class="`chip-${cls}`">{{ label }}</span>
</template>
