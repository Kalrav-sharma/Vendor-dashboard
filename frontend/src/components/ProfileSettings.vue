<script setup>
import { ref } from "vue";
import SetNewPasswordForm from "./SetNewPasswordForm.vue";

defineProps({
  displayName: { type: String, required: true },
  email: { type: String, default: "" },
});

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