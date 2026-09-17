<script setup>
import { computed } from "vue";
import { dtdcStatusLabel, dtdcStatusClass } from "../format.js";

const props = defineProps({ statusType: { type: String, default: null } });
const label = computed(() => dtdcStatusLabel(props.statusType));
const cls = computed(() => dtdcStatusClass(props.statusType));
</script>

<template>
  <span class="chip" :class="`chip-${cls}`">{{ label }}</span>
</template>
