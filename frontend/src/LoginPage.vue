<script setup>
import { ref, onMounted } from "vue";
import { supabase, INTERNAL_ROLES, isRecoveryLink } from "./supabaseClient.js";
import { resolveFunctionError } from "./functionError.js";
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
  // A misrouted reset-password link must not be treated as a normal login.
  if (isRecoveryLink()) {
    window.location.href = "reset-password.html" + window.location.hash;
    return;
  }
  // If already signed in, skip straight to the right dashboard.
  const { data: { session } } = await supabase.auth.getSession();
  if (session) await redirectByRole(session.user.id);
});

async function redirectByRole(userId) {
  const { data: profile } = await supabase.from("profiles").select("role").eq("id", userId).single();
  window.location.href = INTERNAL_ROLES.has(profile?.role) ? "admin.html" : "vendor.html";
}

async function handleSignIn() {
  errorMsg.value = "";
  signingIn.value = true;
  const identifier = email.value.trim();

  if (identifier.includes("@")) {
    // Staff always take this path; a vendor may too, if they'd rather use
    // their real (recovery) email than the shared vendor code.
    const { data, error } = await supabase.auth.signInWithPassword({ email: identifier, password: password.value });
    if (error) {
      errorMsg.value = error.message === "Invalid login credentials" ? "Incorrect email or password." : error.message;
      signingIn.value = false;
      return;
    }
    await redirectByRole(data.user.id);
    return;
  }

  // Vendor code: resolved to the real underlying account AND authenticated
  // server-side by vendor-code-auth, so that real email never reaches this
  // client. setSession below installs the result on THIS shared client, so
  // every other page's requireSession()/getSession() can't tell this apart
  // from a normal email sign-in.
  const { data, error } = await supabase.functions.invoke("vendor-code-auth", {
    body: { action: "signin", vendor_code: identifier, password: password.value },
  });
  if (error || !data?.access_token) {
    errorMsg.value = await resolveFunctionError(data, error);
    signingIn.value = false;
    return;
  }
  const { data: sessionData, error: setErr } = await supabase.auth.setSession({
    access_token: data.access_token,
    refresh_token: data.refresh_token,
  });
  if (setErr || !sessionData?.user) {
    errorMsg.value = "Something went wrong signing in. Please try again.";
    signingIn.value = false;
    return;
  }
  await redirectByRole(sessionData.user.id);
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
  const identifier = resetEmail.value.trim();
  const redirectTo = new URL("reset-password.html", window.location.href).href;

  if (identifier.includes("@")) {
    const { error } = await supabase.auth.resetPasswordForEmail(identifier, { redirectTo });
    sendingReset.value = false;
    // Supabase deliberately doesn't reveal whether the email exists (avoids
    // leaking which emails have accounts) -- show the same message either way.
    if (error) {
      errorMsg.value = error.message;
      return;
    }
    resetSentMsg.value = `If an account exists for ${identifier}, a password reset link has been sent. Check your email.`;
    return;
  }

  // Vendor code: resolved server-side by vendor-code-auth, which always
  // returns the same generic response whether or not it actually resolved
  // to anything -- same anti-enumeration principle as the email path above.
  const { data, error } = await supabase.functions.invoke("vendor-code-auth", {
    body: { action: "reset", identifier, redirect_to: redirectTo },
  });
  sendingReset.value = false;
  if (error) {
    errorMsg.value = await resolveFunctionError(data, error);
    return;
  }
  resetSentMsg.value = data?.message || "If an account exists for that vendor code, a password reset link has been sent.";
}
</script>

<template>
  <div class="auth-shell">
    <div class="auth-card">
      <BrandLogo brand="native" class="login-logo" />

      <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>

      <form v-if="view === 'login'" @submit.prevent="handleSignIn">
        <div class="field">
          <label for="email">Email or Vendor Code</label>
          <input id="email" v-model="email" type="text" required autocomplete="username">
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
          <div class="sub" style="margin-top: -8px;">Enter your email or vendor code and we'll send a reset link to the account's recovery email.</div>
          <div class="field">
            <label for="reset-email">Email or Vendor Code</label>
            <input id="reset-email" v-model="resetEmail" type="text" required autocomplete="username">
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
