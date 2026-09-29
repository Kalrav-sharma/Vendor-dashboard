<script setup>
import { computed, ref } from "vue";
import SummaryKpis from "./SummaryKpis.vue";
import { fmtDateOnly, ticketCategoryLabel, ticketStatusLabel, ticketStatusClass } from "../format.js";

const props = defineProps({
  tickets: { type: Array, required: true },      // this vendor's own tickets, newest first
  poCodeOptions: { type: Array, default: () => [] }, // this vendor's own PO codes, for the optional reference field
  vendorCode: { type: String, required: true },
  vendorName: { type: String, default: "" },
  submitterName: { type: String, default: "" },
  onRaiseTicket: { type: Function, required: true }, // (payload) => { data, error }
});

const CATEGORIES = [
  { key: "po_issue", label: "PO issue" },
  { key: "payment_issue", label: "Payment issue" },
  { key: "dispatch_issue", label: "Dispatch issue" },
  { key: "other", label: "Other" },
];

const category = ref("po_issue");
const poCode = ref("");
const subject = ref("");
const description = ref("");
const submitting = ref(false);
const errorMsg = ref("");
const successMsg = ref("");

const kpiTiles = computed(() => {
  const counts = { open: 0, in_progress: 0, resolved: 0 };
  for (const t of props.tickets) counts[t.status] = (counts[t.status] || 0) + 1;
  return [
    { label: "Open", value: counts.open },
    { label: "In progress", value: counts.in_progress },
    { label: "Resolved", value: counts.resolved },
  ];
});

async function handleSubmit() {
  errorMsg.value = "";
  successMsg.value = "";
  if (!subject.value.trim() || !description.value.trim()) {
    errorMsg.value = "Please fill in both the subject and a description.";
    return;
  }

  submitting.value = true;
  const { error } = await props.onRaiseTicket({
    vendorCode: props.vendorCode,
    vendorName: props.vendorName,
    category: category.value,
    poCode: poCode.value || null,
    subject: subject.value.trim(),
    description: description.value.trim(),
    createdByName: props.submitterName,
  });
  submitting.value = false;

  if (error) {
    errorMsg.value = error.message;
    return;
  }
  successMsg.value = "Ticket raised. Our team will get back to you here.";
  category.value = "po_issue";
  poCode.value = "";
  subject.value = "";
  description.value = "";
}
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div class="panel">
    <h2 style="margin-top: 0;">Raise a new ticket</h2>
    <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
    <div v-if="successMsg" class="form-success">{{ successMsg }}</div>
    <form @submit.prevent="handleSubmit">
      <div style="display: flex; gap: 12px; flex-wrap: wrap;">
        <div class="field" style="flex: 1; min-width: 200px;">
          <label for="ticket-category">Category</label>
          <select id="ticket-category" v-model="category">
            <option v-for="c in CATEGORIES" :key="c.key" :value="c.key">{{ c.label }}</option>
          </select>
        </div>
        <div class="field" style="flex: 1; min-width: 200px;">
          <label for="ticket-po">Related PO code (optional)</label>
          <select id="ticket-po" v-model="poCode">
            <option value="">None</option>
            <option v-for="code in poCodeOptions" :key="code" :value="code">{{ code }}</option>
          </select>
        </div>
      </div>
      <div class="field">
        <label for="ticket-subject">Subject</label>
        <input id="ticket-subject" v-model="subject" type="text" placeholder="Short summary of the issue" required>
      </div>
      <div class="field">
        <label for="ticket-description">Description</label>
        <textarea id="ticket-description" v-model="description" rows="4" placeholder="Describe the issue in detail…" required></textarea>
      </div>
      <button type="submit" class="primary-btn" style="width: auto; padding: 10px 24px;" :disabled="submitting">
        {{ submitting ? "Submitting…" : "Submit ticket" }}
      </button>
    </form>
  </div>

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th>Raised on</th><th>Category</th><th>PO code</th><th>Subject</th><th>Status</th><th>Response</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!tickets.length">
          <td colspan="6" class="empty-state">No tickets raised yet.</td>
        </tr>
        <tr v-for="t in tickets" :key="t.id">
          <td class="mono">{{ fmtDateOnly(t.created_at) }}</td>
          <td>{{ ticketCategoryLabel(t.category) }}</td>
          <td class="mono">{{ t.po_code || "–" }}</td>
          <td>{{ t.subject }}</td>
          <td><span class="chip" :class="`chip-${ticketStatusClass(t.status)}`">{{ ticketStatusLabel(t.status) }}</span></td>
          <td>{{ t.admin_response || "–" }}</td>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
