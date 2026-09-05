<script setup>
import { ref, onMounted } from "vue";
import { supabase } from "./supabaseClient.js";
import SetNewPasswordForm from "./components/SetNewPasswordForm.vue";
import BrandLogo from "./components/BrandLogo.vue";

const status = ref("checking"); // "checking" | "ready" | "invalid"

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

async function handleDone() {
  // Sign out of the recovery session so they log in fresh with the new
  // password, rather than staying silently signed in via the reset link.
  await supabase.auth.signOut();
  window.location.href = "login.html";
}
</script>

<template>
  <div class="auth-shell">
    <div class="auth-card">
      <BrandLogo brand="native" class="login-logo" />
      <h1>Reset your password</h1>
      <div class="sub">
        <template v-if="status === 'checking'">Checking your reset link…</template>
        <template v-else-if="status === 'ready'">Choose a new password for your account.</template>
      </div>

      <SetNewPasswordForm v-if="status === 'ready'" @done="handleDone" />

      <div v-if="status === 'invalid'">
        <div class="form-error">This reset link is invalid or has expired.</div>
        <a href="login.html">Request a new one</a>
      </div>
    </div>
  </div>
</template>
