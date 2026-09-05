<script setup>
import { onMounted, onUnmounted } from "vue";
import { useModal } from "../composables/useModal.js";

const { state, close } = useModal();

function onKeydown(e) {
  if (e.key === "Escape") close();
}
onMounted(() => document.addEventListener("keydown", onKeydown));
onUnmounted(() => document.removeEventListener("keydown", onKeydown));
</script>

<template>
  <div v-if="state.component" class="modal-overlay" @click.self="close">
    <div class="modal-box">
      <button class="modal-close-btn" aria-label="Close" @click="close">&times;</button>
      <div class="modal-title">
        {{ state.title }}
        <span v-if="state.titleCode" class="mono">{{ state.titleCode }}</span>
      </div>
      <div class="modal-body">
        <component :is="state.component" v-bind="state.props" />
      </div>
    </div>
  </div>
</template>
