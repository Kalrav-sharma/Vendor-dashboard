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
      <div class="subtabs">
        <button type="button" class="subtab-item" :class="{ active: theme === 'system' }" @click="chooseTheme('system')">System</button>
        <button type="button" class="subtab-item" :class="{ active: theme === 'light' }" @click="chooseTheme('light')">Light</button>
        <button type="button" class="subtab-item" :class="{ active: theme === 'dark' }" @click="chooseTheme('dark')">Dark</button>
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