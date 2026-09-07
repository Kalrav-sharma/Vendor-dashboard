<script setup>
import { ref, computed } from "vue";
import { supabase } from "../supabaseClient.js";
import { fmtDate } from "../format.js";
import { resolveFunctionError } from "../functionError.js";
import CustomSelect from "./CustomSelect.vue";

const ACCESS_OPTIONS = [
  { value: "vendor", label: "Vendor" },
  { value: "management", label: "Management" },
  { value: "operations", label: "Operations" },
  { value: "finance", label: "Finance" },
];
const ACCESS_DESCRIPTIONS = {
  vendor: "Sees only their own PO Tracking, SKU Level Data and Payment Dashboard.",
  management: "Full portal access, except creating new logins.",
  operations: "PO Tracking + SKU Level Data only, across all vendors.",
  finance: "Payment Dashboard only, across all vendors.",
};

const props = defineProps({
  vendors: { type: Array, required: true },
  team: { type: Array, required: true },
  onVendorsChanged: { type: Function, required: true },
  onTeamChanged: { type: Function, required: true },
  onRevokeVendor: { type: Function, required: true },  // (userId) => Promise<{ok, error?}>
  onRestoreVendor: { type: Function, required: true },
  onDeleteVendor: { type: Function, required: true },
  onRevokeTeam: { type: Function, required: true },
  onRestoreTeam: { type: Function, required: true },
  onDeleteTeam: { type: Function, required: true },
  allowCreate: { type: Boolean, default: true }, // false for a 'management' login -- see AdminApp.vue
});

const ROLE_LABELS = { vendor: "Vendor", management: "Management", operations: "Operations", finance: "Finance" };

// --- Combined list: vendor logins + internal team logins, one table ---
const combinedRows = computed(() =>
  [...props.vendors, ...props.team].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
);

function actionsFor(row) {
  return row.role === "vendor"
    ? { revoke: props.onRevokeVendor, restore: props.onRestoreVendor, del: props.onDeleteVendor }
    : { revoke: props.onRevokeTeam, restore: props.onRestoreTeam, del: props.onDeleteTeam };
}

// --- Create form -- one "access level" picker chooses which fields show
// and which Edge Function gets called (admin-create-vendor for role
// 'vendor', admin-manage-team for the three internal-staff roles). ---
const accessLevel = ref("vendor");
const email = ref("");
const vendorCode = ref("");
const vendorName = ref("");
const contactName = ref("");
const contactMobile = ref("");
const name = ref("");
const creating = ref(false);
const errorMsg = ref("");
const successMsg = ref("");

function resetForm() {
  email.value = ""; vendorCode.value = ""; vendorName.value = "";
  contactName.value = ""; contactMobile.value = ""; name.value = "";
  accessLevel.value = "vendor";
}

async function handleCreate() {
  errorMsg.value = "";
  successMsg.value = "";
  creating.value = true;

  const isVendor = accessLevel.value === "vendor";
  const body = isVendor
    ? {
        email: email.value.trim(), vendor_code: vendorCode.value.trim(), vendor_name: vendorName.value.trim(),
        contact_name: contactName.value.trim(), contact_mobile: contactMobile.value.trim(),
      }
    : { email: email.value.trim(), display_name: name.value.trim(), role: accessLevel.value };

  const { data, error } = await supabase.functions.invoke(
    isVendor ? "admin-create-vendor" : "admin-manage-team", { body }
  );

  creating.value = false;

  if (error || data?.error) {
    errorMsg.value = await resolveFunctionError(data, error);
    return;
  }

  const label = isVendor
    ? (vendorName.value.trim() || vendorCode.value.trim())
    : `${name.value.trim()} -- ${ROLE_LABELS[accessLevel.value]}`;
  successMsg.value = `Login created for ${email.value.trim()} (${label}). ` +
    `Share these with them directly: temporary password "${data.temp_password}" -- they'll be asked to set their own password the first time they log in.`;

  const wasVendor = isVendor;
  resetForm();
  await (wasVendor ? props.onVendorsChanged() : props.onTeamChanged());
}

// --- Revoke / restore / delete ---
const actioningId = ref(null); // row id currently mid-action, for per-row disabling
const actionErrorId = ref(null);
const actionErrorMsg = ref("");

async function handleRevoke(row) {
  actioningId.value = row.id;
  actionErrorId.value = null;
  const result = await actionsFor(row).revoke(row.id);
  actioningId.value = null;
  if (!result.ok) {
    actionErrorId.value = row.id;
    actionErrorMsg.value = result.error;
  }
}

async function handleRestore(row) {
  actioningId.value = row.id;
  actionErrorId.value = null;
  const result = await actionsFor(row).restore(row.id);
  actioningId.value = null;
  if (!result.ok) {
    actionErrorId.value = row.id;
    actionErrorMsg.value = result.error;
  }
}

async function handleDelete(row) {
  const label = row.vendor_name || row.vendor_code || row.email;
  const confirmed = window.confirm(
    `Permanently delete the login for ${label} (${row.email})?\n\nThis cannot be undone. To bring them back later you'd need to create a new login from scratch.` +
    (row.role === "vendor" ? " Their PO/GRN history is not affected." : "")
  );
  if (!confirmed) return;

  actioningId.value = row.id;
  actionErrorId.value = null;
  const result = await actionsFor(row).del(row.id);
  actioningId.value = null;
  if (!result.ok) {
    actionErrorId.value = row.id;
    actionErrorMsg.value = result.error;
  }
}
</script>

<template>
  <div v-if="allowCreate" class="panel">
    <h2>Create login</h2>
    <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
    <div v-if="successMsg" class="form-success">{{ successMsg }}</div>
    <form @submit.prevent="handleCreate">
      <div class="panel-grid">
        <div class="field">
          <label>Access level</label>
          <CustomSelect v-model="accessLevel" :options="ACCESS_OPTIONS" />
        </div>
        <div class="field">
          <label for="a-email">Email</label>
          <input id="a-email" v-model="email" type="email" required>
        </div>

        <template v-if="accessLevel === 'vendor'">
          <div class="field">
            <label for="a-code">Uniware vendor code</label>
            <input id="a-code" v-model="vendorCode" type="text" required placeholder="e.g. Vendor-156">
          </div>
          <div class="field">
            <label for="a-vname">Vendor display name</label>
            <input id="a-vname" v-model="vendorName" type="text" placeholder="e.g. LEXCRU WATER TECH PVT LTD">
          </div>
          <div class="field">
            <label for="a-contact-name">Contact person's name</label>
            <input id="a-contact-name" v-model="contactName" type="text" required placeholder="e.g. Rohan Mehta">
          </div>
          <div class="field">
            <label for="a-contact-mobile">Contact mobile number</label>
            <input id="a-contact-mobile" v-model="contactMobile" type="tel" required placeholder="e.g. 98765 43210">
          </div>
        </template>
        <template v-else>
          <div class="field">
            <label for="a-name">Name</label>
            <input id="a-name" v-model="name" type="text" required placeholder="e.g. Priya Sharma">
          </div>
        </template>
      </div>
      <p class="field-hint">{{ ACCESS_DESCRIPTIONS[accessLevel] }}</p>
      <p class="field-hint">Every login starts with the same temporary password -- they'll be asked to set their own the first time they log in.</p>
      <button type="submit" class="primary-btn" :disabled="creating" style="width: auto; padding: 9px 20px; margin-top: 4px;">
        {{ creating ? "Creating…" : "Create login" }}
      </button>
    </form>
  </div>

  <div class="panel">
    <h2>Logins on the portal</h2>
    <div v-if="!combinedRows.length" class="empty-state">No logins created yet — use the form above.</div>
    <div v-else class="table-card"><div class="table-scroll">
      <table>
        <thead><tr><th>Name</th><th>Email</th><th>Access level</th><th>Contact</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead>
        <tbody>
          <tr v-for="row in combinedRows" :key="row.id">
            <td>{{ row.vendor_name || row.vendor_code || "–" }}</td>
            <td class="mono">{{ row.email || "–" }}</td>
            <td>{{ ROLE_LABELS[row.role] || row.role }}</td>
            <td>
              <template v-if="row.role === 'vendor'">
                <div>{{ row.contact_name || "–" }}</div>
                <div class="mono" style="color: var(--muted); font-size: 0.78rem;">{{ row.contact_mobile || "" }}</div>
              </template>
              <template v-else>–</template>
            </td>
            <td>
              <span class="chip" :class="row.revoked ? 'chip-critical' : 'chip-good'">
                {{ row.revoked ? "Revoked" : "Active" }}
              </span>
            </td>
            <td class="mono">{{ fmtDate(row.created_at) }}</td>
            <td>
              <div style="display: flex; gap: 12px; align-items: center;">
                <button
                  v-if="!row.revoked" class="link-btn-inline" :disabled="actioningId === row.id"
                  @click="handleRevoke(row)"
                >
                  {{ actioningId === row.id ? "Working…" : "Revoke access" }}
                </button>
                <button
                  v-else class="link-btn-inline" :disabled="actioningId === row.id"
                  @click="handleRestore(row)"
                >
                  {{ actioningId === row.id ? "Working…" : "Restore access" }}
                </button>
                <button
                  class="link-btn-inline" :disabled="actioningId === row.id"
                  style="color: var(--critical);"
                  @click="handleDelete(row)"
                >
                  Delete permanently
                </button>
              </div>
              <div v-if="actionErrorId === row.id" class="form-error" style="margin: 6px 0 0;">{{ actionErrorMsg }}</div>
            </td>
          </tr>
        </tbody>
      </table>
    </div></div>
  </div>
</template>
