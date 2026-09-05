<script setup>
import uploadIcon from "../assets/icons/upload.png";

defineProps({
  onClick: { type: Function, required: true },
});
</script>

<template>
  <button class="icon-btn" title="Add invoice (PDF)" aria-label="Add invoice (PDF)" @click.stop="onClick">
    <img :src="uploadIcon" alt="" class="icon-mono">
  </button>
</template>
