<script setup>
import { ref, onMounted } from "vue";
import { supabase } from "./supabaseClient.js";
import BrandLogo from "./components/BrandLogo.vue";

const view = ref("login"); // "login" | "reset"
const email = ref("");
const password = ref("");
const resetEmail = ref("");
const errorMsg = ref("");
const resetSentMsg = ref("");
const signingIn = ref(false);
const sendingReset = ref(false);

onMounted(async () => {
  // If already signed in, skip straight to the right dashboard.
  const { data: { session } } = await supabase.auth.getSession();
  if (session) await redirectByRole(session.user.id);
});

async function redirectByRole(userId) {
  const { data: profile } = await supabase.from("profiles").select("role").eq("id", userId).single();
  window.location.href = profile?.role === "admin" ? "admin.html" : "vendor.html";
}

async function handleSignIn() {
  errorMsg.value = "";
  signingIn.value = true;
  const { data, error } = await supabase.auth.signInWithPassword({
    email: email.value.trim(),
    password: password.value,
  });
  if (error) {
    errorMsg.value = error.message === "Invalid login credentials" ? "Incorrect email or password." : error.message;
    signingIn.value = false;
    return;
  }
  await redirectByRole(data.user.id);
}

function goToReset() {
  errorMsg.value = "";
  resetSentMsg.value = "";
  resetEmail.value = email.value;
  view.value = "reset";
}

function backToLogin() {
  errorMsg.value = "";
  view.value = "login";
}

async function handleSendReset() {
  errorMsg.value = "";
  sendingReset.value = true;
  const redirectTo = new URL("reset-password.html", window.location.href).href;
  const { error } = await supabase.auth.resetPasswordForEmail(resetEmail.value.trim(), { redirectTo });
  sendingReset.value = false;

  // Supabase deliberately doesn't reveal whether the email exists (avoids
  // leaking which emails have accounts) -- show the same message either way.
  if (error) {
    errorMsg.value = error.message;
    return;
  }
  resetSentMsg.value = `If an account exists for ${resetEmail.value.trim()}, a password reset link has been sent. Check your email.`;
}
</script>

<template>
  <div class="auth-shell">
    <div class="auth-card">
      <BrandLogo brand="native" class="login-logo" />

      <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>

      <form v-if="view === 'login'" @submit.prevent="handleSignIn">
        <div class="field">
          <label for="email">Email</label>
          <input id="email" v-model="email" type="email" required autocomplete="username">
        </div>
        <div class="field">
          <label for="password">Password</label>
          <input id="password" v-model="password" type="password" required autocomplete="current-password">
        </div>
        <button type="submit" class="primary-btn" :disabled="signingIn">
          {{ signingIn ? "Signing in…" : "Sign in" }}
        </button>
        <button type="button" class="link-btn" style="margin-top: 12px;" @click="goToReset">Forgot password?</button>
      </form>

      <form v-else @submit.prevent="handleSendReset">
        <div v-if="resetSentMsg" class="form-success">{{ resetSentMsg }}</div>
        <template v-else>
          <div class="sub" style="margin-top: -8px;">Enter your email and we'll send you a link to reset your password.</div>
          <div class="field">
            <label for="reset-email">Email</label>
            <input id="reset-email" v-model="resetEmail" type="email" required autocomplete="username">
          </div>
          <button type="submit" class="primary-btn" :disabled="sendingReset">
            {{ sendingReset ? "Sending…" : "Send reset link" }}
          </button>
        </template>
        <button type="button" class="link-btn" style="margin-top: 12px;" @click="backToLogin">Back to sign in</button>
      </form>

      <div class="powered-by">
        <span>Powered by</span>
        <BrandLogo brand="uc" />
      </div>
    </div>
  </div>
</template>
