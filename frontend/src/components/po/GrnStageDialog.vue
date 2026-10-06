<script setup>
// "Change stage" for a GRN pending PO: ask the vendor for a credit note, or for a corrected
// invoice, on one or more of its short invoices. Each becomes an invoice_vendor_requests row
// the vendor sees on their side until they answer it (see useVendorRequests.js).
import { onMounted, onUnmounted, ref, watch } from "vue";
import { fmtNum } from "../../format.js";
import { useVendorRequests } from "../../composables/useVendorRequests.js";

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  po: { type: Object, default: null },
  shortUploads: { type: Array, required: true }, // grnGap(po).shortUploads: [{ upload, number, invQty, grnQty }]
  vendorName: { type: String, default: "" },
  requesterLabel: { type: String, default: "" },
});
const emit = defineEmits(["update:modelValue"]);

const OPTIONS = [
  { kind: "credit_note", label: "Request credit note", sub: "The shortfall stands -- the vendor issues a credit note against this invoice." },
  { kind: "reupload_invoice", label: "Ask vendor to re-upload invoice", sub: "The invoice itself is wrong -- the vendor uploads a corrected one." },
];

const { createRequests, working } = useVendorRequests();
const kind = ref("credit_note");
const selected = ref([]);
const note = ref("");
const errorMsg = ref("");

// Fresh form each time it opens, every short invoice ticked.
watch(() => props.modelValue, (open) => {
  if (!open) return;
  kind.value = "credit_note";
  selected.value = props.shortUploads.map((s) => s.upload.id);
  note.value = "";
  errorMsg.value = "";
});

function close() {
  if (working.value) return;
  emit("update:modelValue", false);
}
function onKeydown(e) { if (e.key === "Escape" && props.modelValue) close(); }
onMounted(() => document.addEventListener("keydown", onKeydown));
onUnmounted(() => document.removeEventListener("keydown", onKeydown));

async function submit() {
  const uploads = props.shortUploads.filter((s) => selected.value.includes(s.upload.id)).map((s) => s.upload);
  if (!uploads.length) { errorMsg.value = "Pick at least one invoice."; return; }
  const res = await createRequests(uploads, kind.value, note.value, props.requesterLabel);
  if (!res.ok) { errorMsg.value = `Couldn't send the request: ${res.error}`; return; }
  emit("update:modelValue", false);
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue && po" class="modal-overlay" @click.self="close">
      <div class="modal-box modal-box-narrow grn-stage-box">
        <button class="modal-close-btn" aria-label="Close" @click="close">&times;</button>
        <div class="modal-title">Change stage <span class="mono">{{ po.po_code }}</span></div>
        <p class="field-hint">{{ vendorName }} -- invoiced quantity is more than the warehouse has GRN'd.</p>

        <div class="grn-stage-section">Invoice{{ shortUploads.length === 1 ? "" : "s" }}</div>
        <label v-for="s in shortUploads" :key="s.upload.id" class="grn-stage-invoice">
          <input v-model="selected" type="checkbox" :value="s.upload.id" :disabled="shortUploads.length === 1">
          <span class="mono">{{ s.number }}</span>
          <span class="grn-stage-qty mono">Inv {{ fmtNum(s.invQty) }} · GRN {{ fmtNum(s.grnQty) }} · short {{ fmtNum(s.invQty - s.grnQty) }}</span>
        </label>

        <div class="grn-stage-section">Move to</div>
        <label v-for="o in OPTIONS" :key="o.kind" class="grn-stage-option" :class="{ sel: kind === o.kind }">
          <input v-model="kind" type="radio" name="grn-stage-kind" :value="o.kind">
          <span><b>{{ o.label }}</b><small>{{ o.sub }}</small></span>
        </label>

        <div class="field grn-stage-note">
          <label for="grn-stage-note">Note to vendor</label>
          <textarea id="grn-stage-note" v-model="note" rows="3" maxlength="1000" placeholder="e.g. 40 units short-received at Bombay on 3 Oct -- please issue a CN for the difference."></textarea>
        </div>
        <p class="field-hint">The vendor sees this on their next login, on their Payments page, and with an upload button on the invoice.</p>

        <div v-if="errorMsg" class="form-error">{{ errorMsg }}</div>
        <div class="grn-stage-actions">
          <button type="button" class="fin-btn" :disabled="working" @click="close">Cancel</button>
          <button type="button" class="primary-btn" :disabled="working || !selected.length" @click="submit">
            {{ working ? "Sending…" : "Send to vendor" }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
