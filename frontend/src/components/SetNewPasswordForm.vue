<script setup>
import { ref } from "vue";
import { supabase } from "../supabaseClient.js";

defineProps({
  submitLabel: { type: String, default: "Set new password" },
});
const emit = defineEmits(["done"]);

const newPassword = ref("");
const confirmPassword = ref("");
const errorMsg = ref("");
const submitting = ref(false);

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

  // Best-effort: clears must_change_password if it was set (the forced
  // first-login change); a harmless no-op if it wasn't (the "forgot
  // password" email-link flow, where it's likely already false).
  await supabase.rpc("mark_password_changed");

  submitting.value = false;
  emit("done");
}
</script>

<template>
  <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
  <form @submit.prevent="handleSubmit">
    <div class="field">
      <label for="new-password">New password</label>
      <input id="new-password" v-model="newPassword" type="password" required minlength="8" autocomplete="new-password">
    </div>
    <div class="field">
      <label for="confirm-password">Confirm new password</label>
      <input id="confirm-password" v-model="confirmPassword" type="password" required minlength="8" autocomplete="new-password">
    </div>
    <button type="submit" class="primary-btn" :disabled="submitting">
      {{ submitting ? "Setting password…" : submitLabel }}
    </button>
  </form>
</template>
