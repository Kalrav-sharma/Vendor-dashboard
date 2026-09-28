<script setup>
import { ref } from "vue";
import SetNewPasswordForm from "./SetNewPasswordForm.vue";
import { getTheme, setTheme } from "../theme.js";

defineProps({
  displayName: { type: String, required: true },
  email: { type: String, default: "" },
});

const theme = ref(getTheme());
function chooseTheme(value) {
  theme.value = value;
  setTheme(value);
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