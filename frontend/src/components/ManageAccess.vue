<script setup>
import { ref, reactive, computed } from "vue";
import { supabase } from "../supabaseClient.js";
import { fmtDate } from "../format.js";
import { resolveFunctionError } from "../functionError.js";
import CustomSelect from "./CustomSelect.vue";

// Same 5 options everywhere a role gets picked -- creating a brand-new
// login, or changing an existing one's access level inline in the table.
const ACCESS_OPTIONS = [
  { value: "vendor", label: "Vendor" },
  { value: "management", label: "Management" },
  { value: "operations", label: "Operations" },
  { value: "finance", label: "Finance" },
  { value: "admin", label: "Admin" },
];
const ACCESS_DESCRIPTIONS = {
  vendor: "Sees only their own PO Tracking, SKU Level Data and Payment Dashboard.",
  management: "Full portal access, except creating new logins.",
  operations: "PO Tracking + SKU Level Data only, across all vendors.",
  finance: "Payment Dashboard only, across all vendors.",
  admin: "Full portal access, including creating new logins and changing anyone else's access.",
};
const ROLE_LABELS = { vendor: "Vendor", management: "Management", operations: "Operations", finance: "Finance", admin: "Admin" };

const props = defineProps({
  vendors: { type: Array, required: true },
  team: { type: Array, required: true },  // includes 'admin' rows too -- see useTeam.js
  currentUserId: { type: String, default: null }, // the logged-in admin's own id -- keeps their own access-level dropdown read-only
  onVendorsChanged: { type: Function, required: true },
  onTeamChanged: { type: Function, required: true },
  onRevokeVendor: { type: Function, required: true },  // (userId) => Promise<{ok, error?}>
  onRestoreVendor: { type: Function, required: true },
  onDeleteVendor: { type: Function, required: true },
  onRevokeTeam: { type: Function, required: true },
  onRestoreTeam: { type: Function, required: true },
  onDeleteTeam: { type: Function, required: true },
});

// --- Combined list: vendor logins + internal team logins (incl. admins), one table ---
const combinedRows = computed(() =>
  [...props.vendors, ...props.team].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
);

function actionsFor(row) {
  return row.role === "vendor"
    ? { revoke: props.onRevokeVendor, restore: props.onRestoreVendor, del: props.onDeleteVendor }
    : { revoke: props.onRevokeTeam, restore: props.onRestoreTeam, del: props.onDeleteTeam };
}

async function refreshBoth() {
  await props.onVendorsChanged();
  await props.onTeamChanged();
}

// --- Create form -- one "access level" picker chooses which fields show
// and which Edge Function gets called (admin-create-vendor for role
// 'vendor', admin-manage-team for everything else, including 'admin'). ---
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
    : {
        email: email.value.trim(), display_name: name.value.trim(), role: accessLevel.value,
        contact_name: contactName.value.trim(), contact_mobile: contactMobile.value.trim(),
      };

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

// --- Revoke / restore / delete (never available for an 'admin' row --
// see the template's row.role !== 'admin' guard) ---
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

// --- Access level dropdown, right in the table row -- changes a login's
// role immediately via admin-change-access, with no separate button/click
// needed (unless the target is 'vendor', which needs a vendor code + a
// couple of extra fields first -- that case opens the details panel below
// instead of saving right away). Kept separate from "Edit details" (name/
// contact), which never touches the role. ---
const rowAccessSaving = reactive({}); // row.id -> boolean
const rowAccessError = reactive({});  // row.id -> error message

async function handleAccessLevelChange(row, newRole) {
  if (newRole === row.role) return;
  if (newRole === "vendor") {
    startEditDetails(row, "vendor"); // needs vendor_code/contact fields -- can't apply immediately
    return;
  }
  rowAccessSaving[row.id] = true;
  rowAccessError[row.id] = "";
  const { data, error } = await supabase.functions.invoke("admin-change-access", {
    body: {
      user_id: row.id, new_role: newRole,
      vendor_name: row.vendor_name, contact_name: row.contact_name, contact_mobile: row.contact_mobile,
    },
  });
  rowAccessSaving[row.id] = false;
  if (error || data?.error) {
    rowAccessError[row.id] = await resolveFunctionError(data, error);
    return;
  }
  await refreshBoth();
}

// --- Edit details -- name + contact person's name/mobile for ANY login,
// regardless of access type (Kalrav's explicit call: contact info isn't
// vendor-exclusive). Also completes an in-progress "switch to Vendor"
// started from the access-level dropdown above, which needs a vendor
// code + contact fields before it can actually apply. ---
const editingId = ref(null);
const editTargetRole = ref(null); // the role this save applies -- row's own current role for a pure details edit, or 'vendor' when completing a dropdown-triggered switch
const editName = ref("");
const editVendorCode = ref("");
const editContactName = ref("");
const editContactMobile = ref("");
const editSaving = ref(false);
const editError = ref("");

function startEditDetails(row, targetRole = null) {
  editingId.value = row.id;
  editTargetRole.value = targetRole || row.role;
  editName.value = row.vendor_name || "";
  editVendorCode.value = row.vendor_code || "";
  editContactName.value = row.contact_name || "";
  editContactMobile.value = row.contact_mobile || "";
  editError.value = "";
}

function cancelEditDetails() {
  editingId.value = null;
  editError.value = "";
}

async function saveEditDetails(row) {
  editError.value = "";
  editSaving.value = true;

  const body = {
    user_id: row.id, new_role: editTargetRole.value, vendor_name: editName.value.trim(),
    contact_name: editContactName.value.trim(), contact_mobile: editContactMobile.value.trim(),
  };
  if (editTargetRole.value === "vendor") {
    body.vendor_code = editVendorCode.value.trim();
  }

  const { data, error } = await supabase.functions.invoke("admin-change-access", { body });
  editSaving.value = false;
  if (error || data?.error) {
    editError.value = await resolveFunctionError(data, error);
    return;
  }

  editingId.value = null;
  await refreshBoth();
}
</script>

<template>
  <div class="panel">
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
        </template>
        <template v-else>
          <div class="field">
            <label for="a-name">Name</label>
            <input id="a-name" v-model="name" type="text" required placeholder="e.g. Priya Sharma">
          </div>
        </template>
        <div class="field">
          <label for="a-contact-name">Contact person's name{{ accessLevel !== "vendor" ? " (optional)" : "" }}</label>
          <input id="a-contact-name" v-model="contactName" type="text" :required="accessLevel === 'vendor'" placeholder="e.g. Rohan Mehta">
        </div>
        <div class="field">
          <label for="a-contact-mobile">Contact mobile number{{ accessLevel !== "vendor" ? " (optional)" : "" }}</label>
          <input id="a-contact-mobile" v-model="contactMobile" type="tel" :required="accessLevel === 'vendor'" placeholder="e.g. 98765 43210">
        </div>
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
          <template v-for="row in combinedRows" :key="row.id">
          <tr>
            <td>{{ row.vendor_name || row.vendor_code || "–" }}</td>
            <td class="mono">{{ row.email || "–" }}</td>
            <td>
              <span v-if="row.id === currentUserId">{{ ROLE_LABELS[row.role] || row.role }}</span>
              <CustomSelect
                v-else :model-value="row.role" :options="ACCESS_OPTIONS"
                @update:model-value="(v) => handleAccessLevelChange(row, v)"
              />
              <div v-if="rowAccessSaving[row.id]" class="field-hint" style="margin: 4px 0 0;">Saving…</div>
              <div v-if="rowAccessError[row.id]" class="form-error" style="margin: 4px 0 0; font-size: 0.72rem;">{{ rowAccessError[row.id] }}</div>
            </td>
            <td>
              <div>{{ row.contact_name || "–" }}</div>
              <div class="mono" style="color: var(--muted); font-size: 0.78rem;">{{ row.contact_mobile || "" }}</div>
            </td>
            <td>
              <span class="chip" :class="row.revoked ? 'chip-critical' : 'chip-good'">
                {{ row.revoked ? "Revoked" : "Active" }}
              </span>
            </td>
            <td class="mono">{{ fmtDate(row.created_at) }}</td>
            <td>
              <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
                <button class="link-btn-inline" :disabled="actioningId === row.id" @click="startEditDetails(row)">
                  Edit details
                </button>
                <template v-if="row.role !== 'admin'">
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
                </template>
              </div>
              <div v-if="actionErrorId === row.id" class="form-error" style="margin: 6px 0 0;">{{ actionErrorMsg }}</div>
            </td>
          </tr>
          <tr v-if="editingId === row.id">
            <td colspan="7" style="background: var(--paper);">
              <div style="padding: 12px 4px;">
                <p v-if="editTargetRole === 'vendor' && row.role !== 'vendor'" class="field-hint" style="margin: 0 0 8px;">
                  Switching to Vendor -- a Uniware vendor code and contact details are required first.
                </p>
                <div class="panel-grid" style="margin-bottom: 8px;">
                  <div class="field">
                    <label>Name</label>
                    <input v-model="editName" type="text">
                  </div>
                  <div v-if="editTargetRole === 'vendor'" class="field">
                    <label>Uniware vendor code</label>
                    <input v-model="editVendorCode" type="text" required placeholder="e.g. Vendor-156">
                  </div>
                  <div class="field">
                    <label>Contact person's name{{ editTargetRole !== "vendor" ? " (optional)" : "" }}</label>
                    <input v-model="editContactName" type="text" :required="editTargetRole === 'vendor'" placeholder="e.g. Rohan Mehta">
                  </div>
                  <div class="field">
                    <label>Contact mobile number{{ editTargetRole !== "vendor" ? " (optional)" : "" }}</label>
                    <input v-model="editContactMobile" type="tel" :required="editTargetRole === 'vendor'" placeholder="e.g. 98765 43210">
                  </div>
                </div>
                <div v-if="editError" class="form-error" style="margin-bottom: 8px;">{{ editError }}</div>
                <div style="display: flex; gap: 12px; align-items: center;">
                  <button
                    class="primary-btn" style="width: auto; padding: 7px 16px;"
                    :disabled="editSaving" @click="saveEditDetails(row)"
                  >
                    {{ editSaving ? "Saving…" : "Save details" }}
                  </button>
                  <button class="link-btn-inline" @click="cancelEditDetails">Cancel</button>
                </div>
              </div>
            </td>
          </tr>
          </template>
        </tbody>
      </table>
    </div></div>
  </div>
</template>
