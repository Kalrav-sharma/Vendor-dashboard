<script setup>
import BrandLogo from "./BrandLogo.vue";

defineProps({
  brand: { type: String, required: true },
  items: { type: Array, required: true }, // [{ id, label }]
  modelValue: { type: String, required: true },
});
defineEmits(["update:modelValue"]);
</script>

<template>
  <nav class="sidebar">
    <div class="brand">
      <BrandLogo brand="native" class="sidebar-logo" />
      <div class="sidebar-app-name">{{ brand }}</div>
    </div>
    <button
      v-for="item in items" :key="item.id"
      class="nav-item" :class="{ active: modelValue === item.id }"
      @click="$emit('update:modelValue', item.id)"
    >
      {{ item.label }}
    </button>
  </nav>
</template>
