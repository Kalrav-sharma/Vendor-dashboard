<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";

const props = defineProps({
  displayName: { type: String, required: true },
  email: { type: String, default: "" },
  onSignOut: { type: Function, required: true },
});

const open = ref(false);
const rootEl = ref(null);

const initials = computed(() => {
  const first = (props.displayName || "").trim().charAt(0);
  return first ? first.toUpperCase() : "?";
});

function handleDocClick(e) {
  // Fires after the avatar button's own @click (which toggles `open`) in
  // the same bubble phase, so a click ON the button never immediately
  // re-closes what it just opened -- only a click truly outside does.
  if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false;
}
function handleKeydown(e) {
  if (e.key === "Escape") open.value = false;
}
onMounted(() => {
  document.addEventListener("click", handleDocClick);
  document.addEventListener("keydown", handleKeydown);
});
onUnmounted(() => {
  document.removeEventListener("click", handleDocClick);
  document.removeEventListener("keydown", handleKeydown);
});
</script>

<template>
  <div ref="rootEl" class="profile-menu">
    <button class="profile-avatar" :aria-expanded="open" aria-label="Account menu" @click="open = !open">
      {{ initials }}
    </button>

    <div v-if="open" class="profile-dropdown">
      <div class="profile-dropdown-avatar">{{ initials }}</div>
      <div class="profile-dropdown-name">{{ displayName }}</div>
      <div v-if="email" class="profile-dropdown-email mono">{{ email }}</div>
      <button class="profile-dropdown-signout" @click="onSignOut">Sign out</button>
    </div>
  </div>
</template>
