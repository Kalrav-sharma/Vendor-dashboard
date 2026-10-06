<script setup>
// "Close PO" for an Awaiting supply PO: closes it in Uniware itself via the close-uniware-po
// Edge Function (permanent there), with the team's reason recorded in po_close_log.
import { onMounted, onUnmounted, ref, watch } from "vue";
import { supabase } from "../../supabaseClient.js";
import { resolveFunctionError } from "../../functionError.js";
import { fmtMoney, fmtNum, fmtDateOnly } from "../../format.js";

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  po: { type: Object, default: null },
  vendorName: { type: String, default: "" },
});
const emit = defineEmits(["update:modelValue", "closed"]);

const REASONS = [
  { value: "vendor_cannot_supply", label: "Vendor can't supply" },
  { value: "no_longer_needed", label: "No longer needed" },
  { value: "duplicate_po", label: "Duplicate PO" },
  { value: "raised_in_error", label: "Raised in error" },
  { value: "other", label: "Other (add a note)" },
];

const reason = ref("");
const note = ref("");
const confirmed = ref(false);
const working = ref(false);
const errorMsg = ref("");

watch(() => props.modelValue, (open) => {
  if (!open) return;
  reason.value = ""; note.value = ""; confirmed.value = false; errorMsg.value = "";
});

function close() {
  if (working.value) return;
  emit("update:modelValue", false);
}
function onKeydown(e) { if (e.key === "Escape" && props.modelValue) close(); }
onMounted(() => document.addEventListener("keydown", onKeydown));
onUnmounted(() => document.removeEventListener("keydown", onKeydown));

async function submit() {
  errorMsg.value = "";
  if (!reason.value) { errorMsg.value = "Pick a reason."; return; }
  if (reason.value === "other" && !note.value.trim()) { errorMsg.value = "Add a note for Other."; return; }
  working.value = true;
  try {
    const { data, error } = await supabase.functions.invoke("close-uniware-po", {
      body: { po_code: props.po.po_code, reason: reason.value, note: note.value },
    });
    if (error || !data?.ok) {
      errorMsg.value = await resolveFunctionError(data, error);
      return;
    }
    emit("closed", { poCode: props.po.po_code, status: data.status, warning: data.warning || "" });
    emit("update:modelValue", false);
  } finally {
    working.value = false;
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue && po" class="modal-overlay" @click.self="close">
      <div class="modal-box grn-stage-box">
        <button class="modal-close-btn" aria-label="Close" @click="close">&times;</button>
        <div class="modal-title">Close PO in Uniware <span class="mono">{{ po.po_code }}</span></div>
        <p class="field-hint">
          {{ vendorName }} · {{ po.facility }} · raised {{ fmtDateOnly(po.created_at) }} ·
          {{ fmtNum(po.qty_ordered) }} units · {{ fmtMoney(po.total_amount) }}
        </p>

        <div class="field">
          <label for="close-po-reason">Reason</label>
          <select id="close-po-reason" v-model="reason">
            <option value="" disabled>Pick a reason…</option>
            <option v-for="r in REASONS" :key="r.value" :value="r.value">{{ r.label }}</option>
          </select>
        </div>
        <div class="field">
          <label for="close-po-note">Note{{ reason === "other" ? "" : " (optional)" }}</label>
          <textarea id="close-po-note" v-model="note" rows="2" maxlength="1000" placeholder="Kept in the portal's close log -- not sent to Uniware or the vendor."></textarea>
        </div>

        <label class="close-po-confirm">
          <input v-model="confirmed" type="checkbox">
          <span>I understand this <b>closes the PO in Uniware</b> and can't be undone from here.</span>
        </label>

        <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
        <div class="grn-stage-actions">
          <button type="button" class="fin-btn" :disabled="working" @click="close">Cancel</button>
          <button type="button" class="primary-btn close-po-btn" :disabled="working || !confirmed || !reason" @click="submit">
            {{ working ? "Closing in Uniware…" : "Close PO" }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
