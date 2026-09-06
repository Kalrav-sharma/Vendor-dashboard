<script setup>
import { ref } from "vue";
import { supabase } from "../supabaseClient.js";
import { fmtDate } from "../format.js";

const props = defineProps({
  team: { type: Array, required: true },
  onTeamChanged: { type: Function, required: true }, // re-fetch team after creating one
  onRevoke: { type: Function, required: true },  // (userId) => Promise<{ok, error?}>
  onRestore: { type: Function, required: true }, // (userId) => Promise<{ok, error?}>
  onDelete: { type: Function, required: true },  // (userId) => Promise<{ok, error?}>
});

const ROLE_LABELS = { management: "Management", operations: "Operations", finance: "Finance" };
function roleLabel(role) { return ROLE_LABELS[role] || role; }

const email = ref("");
const displayName = ref("");
const role = ref("operations");
const creating = ref(false);
const errorMsg = ref("");
const successMsg = ref("");

async function handleCreate() {
  errorMsg.value = "";
  successMsg.value = "";
  creating.value = true;

  const { data, error } = await supabase.functions.invoke("admin-manage-team", {
    body: {
      email: email.value.trim(),
      display_name: displayName.value.trim(),
      role: role.value,
    },
  });

  creating.value = false;

  if (error || data?.error) {
    errorMsg.value = data?.error || error.message;
    return;
  }

  successMsg.value = `Login created for ${email.value.trim()} (${roleLabel(role.value)}). ` +
    `Share these with them directly: temporary password "${data.temp_password}" -- they'll be asked to set their own password the first time they log in.`;
  email.value = "";
  displayName.value = "";
  role.value = "operations";
  await props.onTeamChanged();
}

// --- Revoke / restore / delete ---
const actioningId = ref(null);
const actionErrorId = ref(null);
const actionErrorMsg = ref("");

async function handleRevoke(m) {
  actioningId.value = m.id;
  actionErrorId.value = null;
  const result = await props.onRevoke(m.id);
  actioningId.value = null;
  if (!result.ok) {
    actionErrorId.value = m.id;
    actionErrorMsg.value = result.error;
  }
}

async function handleRestore(m) {
  actioningId.value = m.id;
  actionErrorId.value = null;
  const result = await props.onRestore(m.id);
  actioningId.value = null;
  if (!result.ok) {
    actionErrorId.value = m.id;
    actionErrorMsg.value = result.error;
  }
}

async function handleDelete(m) {
  const label = m.vendor_name || m.email;
  const confirmed = window.confirm(
    `Permanently delete the login for ${label} (${roleLabel(m.role)})?\n\nThis cannot be undone. To bring them back later you'd need to create a new login from scratch.`
  );
  if (!confirmed) return;

  actioningId.value = m.id;
  actionErrorId.value = null;
  const result = await props.onDelete(m.id);
  actioningId.value = null;
  if (!result.ok) {
    actionErrorId.value = m.id;
    actionErrorMsg.value = result.error;
  }
}
</script>

<template>
  <div class="panel">
    <h2>Create internal team login</h2>
    <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
    <div v-if="successMsg" class="form-success">{{ successMsg }}</div>
    <form @submit.prevent="handleCreate">
      <div class="panel-grid">
        <div class="field">
          <label for="t-email">Email</label>
          <input id="t-email" v-model="email" type="email" required>
        </div>
        <div class="field">
          <label for="t-name">Name</label>
          <input id="t-name" v-model="displayName" type="text" required placeholder="e.g. Priya Sharma">
        </div>
        <div class="field">
          <label for="t-role">Access level</label>
          <select id="t-role" v-model="role" required>
            <option value="management">Management -- full portal access, except creating new logins</option>
            <option value="operations">Operations -- PO Tracking + SKU Level Data only</option>
            <option value="finance">Finance -- Payment Dashboard only</option>
          </select>
        </div>
      </div>
      <p class="field-hint">Every login starts with the same temporary password -- they'll be asked to set their own the first time they log in.</p>
      <button type="submit" class="primary-btn" :disabled="creating" style="width: auto; padding: 9px 20px; margin-top: 4px;">
        {{ creating ? "Creating…" : "Create login" }}
      </button>
    </form>
  </div>

  <div class="panel">
    <h2>Internal team logins</h2>
    <div v-if="!team.length" class="empty-state">No internal team logins created yet — use the form above.</div>
    <div v-else class="table-card"><div class="table-scroll">
      <table>
        <thead><tr><th>Name</th><th>Email</th><th>Access level</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead>
        <tbody>
          <tr v-for="m in team" :key="m.id">
            <td>{{ m.vendor_name || "–" }}</td>
            <td class="mono">{{ m.email || "–" }}</td>
            <td>{{ roleLabel(m.role) }}</td>
            <td>
              <span class="chip" :class="m.revoked ? 'chip-critical' : 'chip-good'">
                {{ m.revoked ? "Revoked" : "Active" }}
              </span>
            </td>
            <td class="mono">{{ fmtDate(m.created_at) }}</td>
            <td>
              <div style="display: flex; gap: 12px; align-items: center;">
                <button
                  v-if="!m.revoked" class="link-btn-inline" :disabled="actioningId === m.id"
                  @click="handleRevoke(m)"
                >
                  {{ actioningId === m.id ? "Working…" : "Revoke access" }}
                </button>
                <button
                  v-else class="link-btn-inline" :disabled="actioningId === m.id"
                  @click="handleRestore(m)"
                >
                  {{ actioningId === m.id ? "Working…" : "Restore access" }}
                </button>
                <button
                  class="link-btn-inline" :disabled="actioningId === m.id"
                  style="color: var(--critical);"
                  @click="handleDelete(m)"
                >
                  Delete permanently
                </button>
              </div>
              <div v-if="actionErrorId === m.id" class="form-error" style="margin: 6px 0 0;">{{ actionErrorMsg }}</div>
            </td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </div>
</template>
