<script setup>
import { computed } from "vue";
import { letsTransportStatusLabel, letsTransportStatusClass } from "../format.js";

const props = defineProps({ statusType: { type: String, default: null } });
const label = computed(() => letsTransportStatusLabel(props.statusType));
const cls = computed(() => letsTransportStatusClass(props.statusType));
</script>

<template>
  <span class="chip" :class="`chip-${cls}`">{{ label }}</span>
</template>
