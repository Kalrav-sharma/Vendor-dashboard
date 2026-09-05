<script setup>
import { ref, onMounted } from "vue";
import { supabase } from "./supabaseClient.js";

const status = ref("checking"); // "checking" | "ready" | "invalid"
const newPassword = ref("");
const confirmPassword = ref("");
const errorMsg = ref("");
const submitting = ref(false);

let sessionReady = false;

function showForm() {
  if (sessionReady) return; // avoid double-handling if both signals fire
  sessionReady = true;
  status.value = "ready";
}

function showInvalid() {
  if (sessionReady) return;
  status.value = "invalid";
}

onMounted(() => {
  // Supabase's client auto-detects the recovery token in this page's URL
  // (the link from the reset email) and fires this event once a session
  // is established from it.
  supabase.auth.onAuthStateChange((event) => {
    if (event === "PASSWORD_RECOVERY") showForm();
  });

  // Fallback in case a session already exists by the time this runs (or
  // the PASSWORD_RECOVERY event fired before the listener above attached).
  supabase.auth.getSession().then(({ data: { session } }) => {
    if (session) showForm();
  });

  // If neither signal shows up within a few seconds, the link was invalid/expired.
  setTimeout(() => { if (!sessionReady) showInvalid(); }, 4000);
});

async function handleSubmit() {
  errorMsg.value = "";

  if (newPassword.value !== confirmPassword.value) {
    errorMsg.value = "Passwords don't match.";
    return;
  }

  submitting.value = true;
  const { error } = await supabase.auth.updateUser({ password: newPassword.value });

  if (error) {
    errorMsg.value = error.message;
    submitting.value = false;
    return;
  }

  // Sign out of the recovery session so they log in fresh with the new
  // password, rather than staying silently signed in via the reset link.
  await supabase.auth.signOut();
  window.location.href = "login.html";
}
</script>

<template>
  <div class="auth-shell">
    <div class="auth-card">
      <h1>Reset your password</h1>
      <div class="sub">
        <template v-if="status === 'checking'">Checking your reset link…</template>
        <template v-else-if="status === 'ready'">Choose a new password for your account.</template>
      </div>

      <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>

      <form v-if="status === 'ready'" @submit.prevent="handleSubmit">
        <div class="field">
          <label for="new-password">New password</label>
          <input id="new-password" v-model="newPassword" type="password" required minlength="8" autocomplete="new-password">
        </div>
        <div class="field">
          <label for="confirm-password">Confirm new password</label>
          <input id="confirm-password" v-model="confirmPassword" type="password" required minlength="8" autocomplete="new-password">
        </div>
        <button type="submit" class="primary-btn" :disabled="submitting">
          {{ submitting ? "Setting password…" : "Set new password" }}
        </button>
      </form>

      <div v-if="status === 'invalid'">
        <div class="form-error">This reset link is invalid or has expired.</div>
        <a href="login.html">Request a new one</a>
      </div>
    </div>
  </div>
</template>
