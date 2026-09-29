<script setup>
import { ref } from "vue";
import { fmtDateOnly, ticketCategoryLabel } from "../format.js";

const props = defineProps({
  ticket: { type: Object, required: true },
  vendorLabelText: { type: String, default: "" },
  responderLabel: { type: String, default: "" }, // current staff member's display name, recorded on the response
  onUpdateTicket: { type: Function, required: true }, // (id, { status, adminResponse, respondedByName }) => { data, error }
});

const STATUS_OPTIONS = [
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In progress" },
  { value: "resolved", label: "Resolved" },
];

const status = ref(props.ticket.status);
const response = ref(props.ticket.admin_response || "");
const saving = ref(false);
const errorMsg = ref("");
const savedMsg = ref("");

async function handleSave() {
  errorMsg.value = "";
  savedMsg.value = "";
  saving.value = true;
  const { error } = await props.onUpdateTicket(props.ticket.id, {
    status: status.value,
    adminResponse: response.value.trim() || null,
    respondedByName: props.responderLabel,
  });
  saving.value = false;
  if (error) {
    errorMsg.value = error.message;
    return;
  }
  savedMsg.value = "Saved.";
}
</script>

<template>
  <div class="field">
    <label>Vendor</label>
    <div>{{ vendorLabelText || ticket.vendor_code }}</div>
  </div>
  <div style="display: flex; gap: 24px; margin-bottom: 14px;">
    <div><label class="field-hint" style="margin: 0;">Category</label><div>{{ ticketCategoryLabel(ticket.category) }}</div></div>
    <div v-if="ticket.po_code"><label class="field-hint" style="margin: 0;">PO code</label><div class="mono">{{ ticket.po_code }}</div></div>
    <div><label class="field-hint" style="margin: 0;">Raised on</label><div>{{ fmtDateOnly(ticket.created_at) }}</div></div>
    <div v-if="ticket.created_by_name"><label class="field-hint" style="margin: 0;">Raised by</label><div>{{ ticket.created_by_name }}</div></div>
  </div>

  <div class="field">
    <label>Subject</label>
    <div>{{ ticket.subject }}</div>
  </div>
  <div class="field">
    <label>Description</label>
    <div style="white-space: pre-wrap;">{{ ticket.description }}</div>
  </div>

  <hr style="border: none; border-top: 1px solid var(--line); margin: 18px 0;">

  <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
  <div v-if="savedMsg" class="form-success">{{ savedMsg }}</div>

  <div class="field">
    <label for="ticket-status">Status</label>
    <select id="ticket-status" v-model="status">
      <option v-for="o in STATUS_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
    </select>
  </div>
  <div class="field">
    <label for="ticket-response">Response to vendor</label>
    <textarea id="ticket-response" v-model="response" rows="4" placeholder="Visible to the vendor once saved…"></textarea>
  </div>
  <button type="button" class="primary-btn" style="width: auto; padding: 10px 24px;" :disabled="saving" @click="handleSave">
    {{ saving ? "Saving…" : "Save" }}
  </button>
</template>
