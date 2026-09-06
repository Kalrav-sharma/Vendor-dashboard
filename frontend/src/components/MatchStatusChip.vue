<script setup>
import { computed } from "vue";
import { matchStatusLabel, matchStatusClass } from "../format.js";

const props = defineProps({ status: { type: String, default: null } });
const label = computed(() => matchStatusLabel(props.status));
const cls = computed(() => matchStatusClass(props.status));
</script>

<template>
  <span class="chip" :class="`chip-${cls}`">{{ label }}</span>
</template>
