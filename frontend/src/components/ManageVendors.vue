<script setup>
import { ref } from "vue";
import { supabase } from "../supabaseClient.js";
import { fmtDate } from "../format.js";

const props = defineProps({
  vendors: { type: Array, required: true },
  onVendorsChanged: { type: Function, required: true }, // re-fetch vendors after creating one
  onRevoke: { type: Function, required: true },  // (userId) => Promise<{ok, error?}>
  onRestore: { type: Function, required: true }, // (userId) => Promise<{ok, error?}>
  onDelete: { type: Function, required: true },  // (userId) => Promise<{ok, error?}>
});

const email = ref("");
const password = ref("");
const vendorCode = ref("");
const vendorName = ref("");
const creating = ref(false);
const errorMsg = ref("");
const successMsg = ref("");

async function handleCreate() {
  errorMsg.value = "";
  successMsg.value = "";
  creating.value = true;

  const { data, error } = await supabase.functions.invoke("admin-create-vendor", {
    body: {
      email: email.value.trim(),
      password: password.value,
      vendor_code: vendorCode.value.trim(),
      vendor_name: vendorName.value.trim(),
    },
  });

  creating.value = false;

  if (error || data?.error) {
    errorMsg.value = data?.error || error.message;
    return;
  }

  successMsg.value = `Login created for ${email.value.trim()} (${vendorName.value.trim() || vendorCode.value.trim()}). Share the email + temporary password with the vendor directly — this page will not show the password again.`;
  email.value = "";
  password.value = "";
  vendorCode.value = "";
  vendorName.value = "";
  await props.onVendorsChanged();
}

// --- Revoke / restore / delete ---
const actioningId = ref(null); // vendor id currently mid-action, for per-row disabling
const actionErrorId = ref(null);
const actionErrorMsg = ref("");

async function handleRevoke(v) {
  actioningId.value = v.id;
  actionErrorId.value = null;
  const result = await props.onRevoke(v.id);
  actioningId.value = null;
  if (!result.ok) {
    actionErrorId.value = v.id;
    actionErrorMsg.value = result.error;
  }
}

async function handleRestore(v) {
  actioningId.value = v.id;
  actionErrorId.value = null;
  const result = await props.onRestore(v.id);
  actioningId.value = null;
  if (!result.ok) {
    actionErrorId.value = v.id;
    actionErrorMsg.value = result.error;
  }
}

async function handleDelete(v) {
  const label = v.vendor_name || v.vendor_code;
  const confirmed = window.confirm(
    `Permanently delete the login for ${label} (${v.email})?\n\nThis cannot be undone. To bring them back later you'd need to create a new login from scratch. Their PO/GRN history is not affected.`
  );
  if (!confirmed) return;

  actioningId.value = v.id;
  actionErrorId.value = null;
  const result = await props.onDelete(v.id);
  actioningId.value = null;
  if (!result.ok) {
    actionErrorId.value = v.id;
    actionErrorMsg.value = result.error;
  }
}
</script>

<template>
  <div class="panel">
    <h2>Create vendor login</h2>
    <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
    <div v-if="successMsg" class="form-success">{{ successMsg }}</div>
    <form @submit.prevent="handleCreate">
      <div class="panel-grid">
        <div class="field">
          <label for="v-email">Email</label>
          <input id="v-email" v-model="email" type="email" required>
        </div>
        <div class="field">
          <label for="v-password">Temporary password</label>
          <input id="v-password" v-model="password" type="text" required minlength="8" placeholder="min 8 characters">
        </div>
        <div class="field">
          <label for="v-code">Uniware vendor code</label>
          <input id="v-code" v-model="vendorCode" type="text" required placeholder="e.g. Vendor-156">
        </div>
        <div class="field">
          <label for="v-name">Vendor display name</label>
          <input id="v-name" v-model="vendorName" type="text" placeholder="e.g. LEXCRU WATER TECH PVT LTD">
        </div>
      </div>
      <button type="submit" class="primary-btn" :disabled="creating" style="width: auto; padding: 9px 20px; margin-top: 16px;">
        {{ creating ? "Creating…" : "Create login" }}
      </button>
    </form>
  </div>

  <div class="panel">
    <h2>Vendors on the portal</h2>
    <div v-if="!vendors.length" class="empty-state">No vendor logins created yet — use the form above.</div>
    <div v-else class="table-card"><div class="table-scroll">
      <table>
        <thead><tr><th>Vendor name</th><th>Email</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead>
        <tbody>
          <tr v-for="v in vendors" :key="v.id">
            <td>{{ v.vendor_name || v.vendor_code }}</td>
            <td class="mono">{{ v.email || "–" }}</td>
            <td>
              <span class="chip" :class="v.revoked ? 'chip-critical' : 'chip-good'">
                {{ v.revoked ? "Revoked" : "Active" }}
              </span>
            </td>
            <td class="mono">{{ fmtDate(v.created_at) }}</td>
            <td>
              <div style="display: flex; gap: 12px; align-items: center;">
                <button
                  v-if="!v.revoked" class="link-btn-inline" :disabled="actioningId === v.id"
                  @click="handleRevoke(v)"
                >
                  {{ actioningId === v.id ? "Working…" : "Revoke access" }}
                </button>
                <button
                  v-else class="link-btn-inline" :disabled="actioningId === v.id"
                  @click="handleRestore(v)"
                >
                  {{ actioningId === v.id ? "Working…" : "Restore access" }}
                </button>
                <button
                  class="link-btn-inline" :disabled="actioningId === v.id"
                  style="color: var(--critical);"
                  @click="handleDelete(v)"
                >
                  Delete permanently
                </button>
              </div>
              <div v-if="actionErrorId === v.id" class="form-error" style="margin: 6px 0 0;">{{ actionErrorMsg }}</div>
            </td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </div>
</template>
