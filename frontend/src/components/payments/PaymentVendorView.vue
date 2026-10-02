<script setup>
// Finance › Vendor Payments: pick one vendor and the same KPI tiles + breakdown sections as
// the executive summary recompute for just that vendor (same usePaymentSummary.js maths, so
// a vendor's figures are always a slice of the all-vendor ones). The default slot renders
// below the sections -- AdminApp puts that vendor's invoice table there.
import { computed } from "vue";
import { paymentLedger, summarise } from "../../composables/usePaymentSummary.js";
import CustomSelect from "../CustomSelect.vue";
import PaymentSummaryPanels from "./PaymentSummaryPanels.vue";

const props = defineProps({
  modelValue: { type: String, default: "" }, // selected vendor_code
  vendorOptions: { type: Array, required: true }, // [{ value, label }]
  uploads: { type: Array, required: true }, // allUploads, every vendor -- scoped here
  posWithPayment: { type: Array, default: () => [] },
  posNeedingInvoice: { type: Array, default: () => [] },
  onOpenPo: { type: Function, required: true },
});
const emit = defineEmits(["update:modelValue"]);

const code = computed(() => props.modelValue);
const mine = (list) => list.filter((x) => x.vendor_code === code.value);
const summary = computed(() => summarise(
  paymentLedger(mine(props.uploads), mine(props.posWithPayment)),
  mine(props.posNeedingInvoice),
));

const idx = computed(() => props.vendorOptions.findIndex((o) => o.value === code.value));
function step(d) {
  const next = props.vendorOptions[idx.value + d];
  if (next) emit("update:modelValue", next.value);
}
</script>

<template>
  <div class="pay-vendor-bar">
    <div class="field">
      <label>Vendor</label>
      <CustomSelect
        :model-value="modelValue"
        :options="vendorOptions.length ? vendorOptions : [{ value: '', label: 'No vendors with payment data' }]"
        @update:model-value="(v) => emit('update:modelValue', v)"
      />
    </div>
    <div class="pay-vendor-nav">
      <button type="button" :disabled="idx <= 0" title="Previous vendor" @click="step(-1)">‹ Prev</button>
      <button type="button" :disabled="idx < 0 || idx >= vendorOptions.length - 1" title="Next vendor" @click="step(1)">Next ›</button>
    </div>
  </div>

  <div v-if="!modelValue" class="pay-empty">Select a vendor to see their payment summary.</div>
  <template v-else>
    <PaymentSummaryPanels :summary="summary" :on-open-po="onOpenPo" />
    <slot />
  </template>
</template>
