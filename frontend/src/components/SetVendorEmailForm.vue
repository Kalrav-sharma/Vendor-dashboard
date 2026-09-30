<script setup>
import { ref } from "vue";
import { resolveFunctionError } from "../functionError.js";
import { supabase } from "../supabaseClient.js";

defineProps({
  submitLabel: { type: String, default: "Save email and continue" },
});
const emit = defineEmits(["done"]);

const newEmail = ref("");
const confirmEmail = ref("");
const errorMsg = ref("");
const submitting = ref(false);

async function handleSubmit() {
  errorMsg.value = "";

  if (newEmail.value.trim().toLowerCase() !== confirmEmail.value.trim().toLowerCase()) {
    errorMsg.value = "Emails don't match.";
    return;
  }

  submitting.value = true;
  const { data, error } = await supabase.functions.invoke("vendor-change-email", {
    body: { new_email: newEmail.value.trim() },
  });
  if (error || !data?.ok) {
    errorMsg.value = await resolveFunctionError(data, error);
    submitting.value = false;
    return;
  }

  submitting.value = false;
  emit("done");
}
</script>

<template>
  <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
  <form @submit.prevent="handleSubmit">
    <div class="field">
      <label for="new-email">Your email</label>
      <input id="new-email" v-model="newEmail" type="email" required autocomplete="email">
    </div>
    <div class="field">
      <label for="confirm-email">Confirm email</label>
      <input id="confirm-email" v-model="confirmEmail" type="email" required autocomplete="email">
    </div>
    <button type="submit" class="primary-btn" :disabled="submitting">
      {{ submitting ? "Saving…" : submitLabel }}
    </button>
  </form>
</template>
