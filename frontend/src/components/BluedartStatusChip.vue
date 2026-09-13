<script setup>
import { computed } from "vue";
import { bluedartStatusLabel, bluedartStatusClass } from "../format.js";

const props = defineProps({ statusType: { type: String, default: null } });
const label = computed(() => bluedartStatusLabel(props.statusType));
const cls = computed(() => bluedartStatusClass(props.statusType));
</script>

<template>
  <span class="chip" :class="`chip-${cls}`">{{ label }}</span>
</template>
