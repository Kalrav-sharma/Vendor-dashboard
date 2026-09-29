<script setup>
import { ref, computed } from "vue";
import SetNewPasswordForm from "./SetNewPasswordForm.vue";
import CustomSelect from "./CustomSelect.vue";
import { getTheme, setTheme } from "../theme.js";
import { getViewOverride, getPreviewVendorCode, getPreviewRole, setViewOverride, setPreviewVendorCode, setPreviewRole, clearViewOverride } from "../viewOverride.js";

const props = defineProps({
  displayName: { type: String, required: true },
  email: { type: String, default: "" },
  role: { type: String, default: "" }, // real DB role -- gates "Switch view" below to admins only
  vendors: { type: Array, default: () => [] }, // [{code, label}] -- every vendor_code seen in real PO data, not just ones with a login account (see AdminApp.vue's poVendorOptions)
});

const theme = ref(getTheme());
function chooseTheme(value) {
  theme.value = value;
  setTheme(value);
}

// Admin-only convenience so building/testing both the Vendor and Management
// UIs doesn't need a second login. See viewOverride.js -- this never
// changes what RLS actually returns, just which app shell (and, once a
// vendor is picked below, which vendor_code's rows) an admin's own session
// is currently pointed at.
const isAdmin = computed(() => props.role === "admin");
const currentView = ref(
  getViewOverride() === "vendor" ? "vendor" :
  getPreviewRole() === "finance" ? "finance" :
  "management"
);
// Shown as soon as "Vendor" is clicked, or already, if that's the view
// this modal was opened from -- picking one is what actually navigates.
const pickingVendor = ref(currentView.value === "vendor");
// Pre-selects Lexcru (Vendor-156) -- the project's original vendor account
// and the one admin testing is normally done against -- so there's already
// a sensible choice highlighted instead of a blank picker. This is only
// ever the CustomSelect's initial display value; nothing is actually
// stored or fetched until a vendor is explicitly clicked (goToVendor).
const LEXCRU_VENDOR_CODE = "Vendor-156";
const defaultVendorCode = props.vendors.some(v => v.code === LEXCRU_VENDOR_CODE)
  ? LEXCRU_VENDOR_CODE
  : props.vendors[0]?.code || "";
const selectedVendorCode = ref(getPreviewVendorCode() || defaultVendorCode);

function chooseView(view) {
  if (view === "management") {
    clearViewOverride();
    window.location.href = "admin.html";
    return;
  }
  if (view === "finance") {
    // Reload (not just a local flag flip) -- keeps this identical to the
    // Vendor path below and to how the real page always picks up a fresh
    // navItems set on mount, rather than needing this modal to reach back
    // into AdminApp.vue's own state.
    clearViewOverride();
    setPreviewRole("finance");
    window.location.href = "admin.html";
    return;
  }
  pickingVendor.value = true;
}
function goToVendor(code) {
  selectedVendorCode.value = code;
  clearViewOverride();
  setViewOverride("vendor");
  setPreviewVendorCode(code);
  window.location.href = "vendor.html";
}

const changingPassword = ref(false);
const passwordUpdated = ref(false);

function handlePasswordUpdated() {
  changingPassword.value = false;
  passwordUpdated.value = true;
}
</script>

<template>
  <div class="profile-settings">
    <div class="profile-settings-details">
      <div class="profile-settings-row">
        <span>Name</span>
        <strong>{{ displayName }}</strong>
      </div>
      <div v-if="email" class="profile-settings-row">
        <span>Email</span>
        <strong>{{ email }}</strong>
      </div>
    </div>

    <section v-if="isAdmin" class="profile-settings-appearance">
      <h2>Switch view</h2>
      <div class="appearance-picker" role="group" aria-label="App view">
        <button type="button" class="appearance-option" :class="{ active: currentView === 'management' }" :aria-pressed="currentView === 'management'" @click="chooseView('management')">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 2.5 16 5v5c0 4-2.5 6.5-6 7.5-3.5-1-6-3.5-6-7.5V5l6-2.5Z" /></svg>
          <span>Management</span>
        </button>
        <button type="button" class="appearance-option" :class="{ active: currentView === 'finance' }" :aria-pressed="currentView === 'finance'" @click="chooseView('finance')">
          <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M10 6.2v7.6M12.3 8.1c0-1-1-1.7-2.3-1.7s-2.3.6-2.3 1.6c0 2.3 4.6 1.1 4.6 3.4 0 1-1 1.7-2.3 1.7s-2.3-.7-2.3-1.7" /></svg>
          <span>Finance</span>
        </button>
        <button type="button" class="appearance-option" :class="{ active: currentView === 'vendor' }" :aria-pressed="currentView === 'vendor'" @click="chooseView('vendor')">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 6.5 10 3l7 3.5-7 3.5-7-3.5Z" /><path d="M3 6.5v7L10 17l7-3.5v-7" /><path d="M10 10v7" /></svg>
          <span>Vendor</span>
        </button>
      </div>
      <div v-if="pickingVendor" class="field" style="margin-top: 10px;">
        <label>Preview as</label>
        <CustomSelect
          v-if="vendors.length"
          :model-value="selectedVendorCode"
          :options="vendors.map(v => ({ value: v.code, label: v.label }))"
          @update:model-value="goToVendor"
        />
        <div v-else class="field-hint">No vendor purchase orders exist yet.</div>
      </div>
    </section>

    <section class="profile-settings-appearance">
      <h2>Appearance</h2>
      <div class="appearance-picker" role="group" aria-label="Color theme">
        <button type="button" class="appearance-option" :class="{ active: theme === 'system' }" :aria-pressed="theme === 'system'" @click="chooseTheme('system')">
          <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="3" width="15" height="10.5" rx="1.5" /><path d="M7 17h6M10 13.5V17" /></svg>
          <span>System</span>
        </button>
        <button type="button" class="appearance-option" :class="{ active: theme === 'light' }" :aria-pressed="theme === 'light'" @click="chooseTheme('light')">
          <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.5" /><path d="M10 1.8v2M10 16.2v2M18.2 10h-2M3.8 10h-2m14-5.8-1.4 1.4M5.6 14.4l-1.4 1.4m11.6 0-1.4-1.4M5.6 5.6 4.2 4.2" /></svg>
          <span>Light</span>
        </button>
        <button type="button" class="appearance-option" :class="{ active: theme === 'dark' }" :aria-pressed="theme === 'dark'" @click="chooseTheme('dark')">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16.5 12.2A7.2 7.2 0 0 1 7.8 3.5 7.3 7.3 0 1 0 16.5 12.2Z" /></svg>
          <span>Dark</span>
        </button>
      </div>
    </section>

    <section class="profile-settings-password">
      <h2>Password</h2>
      <div v-if="passwordUpdated" class="form-success">Password updated.</div>
      <SetNewPasswordForm
        v-if="changingPassword"
        submit-label="Update password"
        @done="handlePasswordUpdated"
      />
      <button v-else class="profile-settings-change" @click="changingPassword = true; passwordUpdated = false">
        Change password
      </button>
    </section>
  </div>
</template>