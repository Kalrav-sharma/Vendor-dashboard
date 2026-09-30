<script setup>
import BrandLogo from "./BrandLogo.vue";

defineProps({
  brand: { type: String, required: true },
  // Wordmark lockup: brand text starts under the Native logo's "T" stem.
  lockup: { type: Boolean, default: false },
  items: { type: Array, required: true }, // [{ id, label, badge? }] -- badge: a small count pill beside the label, omitted when falsy
  modelValue: { type: String, required: true },
});
defineEmits(["update:modelValue"]);
</script>

<template>
  <nav class="sidebar">
    <div class="brand" :class="{ lockup }">
      <BrandLogo brand="native" class="sidebar-logo" />
      <BrandLogo v-if="lockup" brand="connect" class="sidebar-wordmark" />
      <div v-else class="sidebar-app-name">{{ brand }}</div>
    </div>
    <button
      v-for="item in items" :key="item.id"
      class="nav-item" :class="{ active: modelValue === item.id }"
      @click="$emit('update:modelValue', item.id)"
    >
      <span>{{ item.label }}</span>
      <span v-if="item.badge" class="nav-badge">{{ item.badge }}</span>
    </button>
    <div class="sidebar-account">
      <slot name="account" />
    </div>
  </nav>
</template>
