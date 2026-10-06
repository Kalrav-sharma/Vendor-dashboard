<script setup>
// "Why?" box for one invoice on the Payments page: why its reconciliation chip says what it
// says (InvoiceCheckList.vue), so a vendor knows exactly what's wrong before acting.
import { onMounted, onUnmounted } from "vue";
import { reconciliationLabel } from "../reconciliation.js";
import InvoiceCheckList from "./InvoiceCheckList.vue";

const props = defineProps({ row: { type: Object, default: null } });
const emit = defineEmits(["close"]);
function onKeydown(e) { if (e.key === "Escape" && props.row) emit("close"); }
onMounted(() => document.addEventListener("keydown", onKeydown));
onUnmounted(() => document.removeEventListener("keydown", onKeydown));
</script>

<template>
  <Teleport to="body">
    <div v-if="row" class="modal-overlay" @click.self="emit('close')">
      <div class="modal-box icl-box" role="dialog" aria-labelledby="icl-title">
        <button class="modal-close-btn" aria-label="Close" @click="emit('close')">&times;</button>
        <div id="icl-title" class="modal-title">
          Invoice <span class="mono">{{ row.match_details?.extracted?.invoice_number || "–" }}</span>
          <span class="chip" :class="`chip-${reconciliationLabel(row).cls}`">{{ reconciliationLabel(row).text }}</span>
        </div>
        <p class="field-hint">PO <span class="mono">{{ row.po_code }}</span> -- each invoice is checked against the PO and what the warehouse received (GRN).</p>
        <InvoiceCheckList :row="row" />
      </div>
    </div>
  </Teleport>
</template>
