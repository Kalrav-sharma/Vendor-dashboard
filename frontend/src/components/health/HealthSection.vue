<script setup>
// One numbered Health Card section ("1. Inventory View" ...): the header toggles its views.
// v-show, not v-if, so a collapsed section keeps its toggles/drill-downs as they were.
// Open/collapsed is remembered per section in localStorage (this browser only).
import { ref, watch } from "vue";

const props = defineProps({
  id: { type: String, required: true },
  title: { type: String, required: true },
});
const KEY = `hc-collapsed:${props.id}`;
const collapsed = ref(localStorage.getItem(KEY) === "1");
watch(collapsed, v => (v ? localStorage.setItem(KEY, "1") : localStorage.removeItem(KEY)));
</script>

<template>
  <section class="hc-section" :class="{ collapsed }">
    <h2 class="hc-section-title">
      <button type="button" class="hc-section-toggle" :aria-expanded="!collapsed" @click="collapsed = !collapsed">
        <span class="hc-chev" aria-hidden="true">▾</span>{{ title }}
      </button>
    </h2>
    <div v-show="!collapsed"><slot /></div>
  </section>
</template>
