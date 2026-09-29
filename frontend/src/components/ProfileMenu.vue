<script setup>
import { onMounted, onUnmounted, ref } from "vue";
import { useModal } from "../composables/useModal.js";
import ProfileSettings from "./ProfileSettings.vue";

const props = defineProps({
  displayName: { type: String, required: true },
  email: { type: String, default: "" },
  access: { type: String, default: "" },
  role: { type: String, default: "" },
  vendors: { type: Array, default: () => [] }, // [{code, label}] -- admin-only, for Settings > Switch view
  onSignOut: { type: Function, required: true },
});

const open = ref(false);
const rootEl = ref(null);
const { open: openModal } = useModal();

function handleDocClick(e) {
  // Fires after the avatar button's own @click (which toggles `open`) in
  // the same bubble phase, so a click ON the button never immediately
  // re-closes what it just opened -- only a click truly outside does.
  if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false;
}
function handleKeydown(e) {
  if (e.key === "Escape") open.value = false;
}
function showProfile() {
  open.value = false;
  openModal("Settings", ProfileSettings, {
    displayName: props.displayName,
    email: props.email,
    role: props.role,
    vendors: props.vendors,
  }, null, "narrow");
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
    <button class="profile-trigger" :aria-expanded="open" :aria-label="`Account menu for ${displayName}`" @click="open = !open">
      <span class="profile-trigger-icon" aria-hidden="true">
        <svg viewBox="0 0 20 20" focusable="false">
          <circle cx="10" cy="6.5" r="3" />
          <path d="M4.5 17v-1.2a5.5 5.5 0 0 1 11 0V17z" />
        </svg>
      </span>
      <span>{{ displayName || "Profile" }}</span>
    </button>

    <div v-if="open" class="profile-dropdown">
      <div class="profile-dropdown-name">{{ displayName }}</div>
      <div v-if="access" class="profile-dropdown-access">{{ access }}</div>
      <div v-if="email" class="profile-dropdown-email mono">{{ email }}</div>
      <div class="profile-dropdown-actions">
        <button class="profile-dropdown-action" @click="showProfile">Settings</button>
        <button class="profile-dropdown-action logout" @click="onSignOut">Log out</button>
      </div>
    </div>
  </div>
</template>
