<script setup>
import { reactive, ref, computed, watch } from "vue";
import { supabase } from "../supabaseClient.js";
import { fmtNum } from "../format.js";

const props = defineProps({
  poItemsByPo: { type: Object, required: true }, // po_code -> [po_items row, ...] (all vendors, internal-staff view)
  vendorLabel: { type: Function, default: null }, // (code) => string, for display only
  onDispatched: { type: Function, default: null }, // () => void -- called after at least one line saves successfully
});

const COURIER_OPTIONS = [
  { value: "bluedart", label: "Bluedart" },
  { value: "dtdc", label: "DTDC" },
];

const poCodeInput = ref("");
const qtyInputs = reactive({});      // item_sku -> typed quantity to dispatch now
const awbInputs = reactive({});      // item_sku -> typed AWB
const courierInputs = reactive({});  // item_sku -> selected courier, defaults to Bluedart
const rowErrors = reactive({});      // item_sku -> error message
const rowDone = reactive({});        // item_sku -> true once successfully saved this session
const submitting = ref(false);
const formError = ref("");

const pendingItems = computed(() => {
  const code = poCodeInput.value.trim();
  if (!code) return null;
  const items = props.poItemsByPo[code];
  if (!items) return [];
  return items.filter((it) => Number(it.pending_quantity) > 0);
});

// Seeds a default courier ("Bluedart") for every pending row's dropdown
// as soon as it appears -- same reasoning as DispatchPlanningTable.vue's
// identical watcher: a plain v-model against an unset reactive key
// renders with no option selected until the user touches it themselves.
watch(pendingItems, (items) => {
  for (const it of items || []) {
    if (courierInputs[it.item_sku] === undefined) courierInputs[it.item_sku] = "bluedart";
  }
});

async function handleSubmit() {
  const items = pendingItems.value || [];
  const toSubmit = items.filter((it) => !rowDone[it.item_sku] && Number(qtyInputs[it.item_sku]) > 0);

  formError.value = "";
  if (!toSubmit.length) {
    formError.value = "Enter a quantity for at least one SKU before confirming.";
    return;
  }

  // Client-side pre-check mirrors the server's own validation (which is
  // still the real gate -- this just avoids a round trip for the common
  // typo of overshooting what's actually pending).
  for (const it of toSubmit) {
    const qty = Number(qtyInputs[it.item_sku]);
    const awb = (awbInputs[it.item_sku] || "").trim();
    rowErrors[it.item_sku] = "";
    if (qty > Number(it.pending_quantity)) {
      rowErrors[it.item_sku] = `Cannot exceed pending quantity (${fmtNum(it.pending_quantity)}).`;
    } else if (!awb) {
      rowErrors[it.item_sku] = "AWB/Tracking ID is required.";
    }
  }
  if (toSubmit.some((it) => rowErrors[it.item_sku])) return;

  submitting.value = true;
  let anySucceeded = false;
  const today = new Date().toISOString().slice(0, 10);

  for (const it of toSubmit) {
    const { error } = await supabase.rpc("manual_confirm_dispatch", {
      p_po_code: poCodeInput.value.trim(),
      p_item_sku: it.item_sku,
      p_awb_number: awbInputs[it.item_sku].trim(),
      p_courier: courierInputs[it.item_sku] || "bluedart",
      p_dispatched_qty: Number(qtyInputs[it.item_sku]),
      p_dispatched_date: today,
    });
    if (error) {
      rowErrors[it.item_sku] = error.message;
    } else {
      rowDone[it.item_sku] = true;
      anySucceeded = true;
    }
  }
  submitting.value = false;

  if (anySucceeded && props.onDispatched) await props.onDispatched();
}
</script>

<template>
  <div class="field" style="max-width: 340px;">
    <label for="manual-dispatch-po-code">PO code</label>
    <input id="manual-dispatch-po-code" v-model="poCodeInput" type="text" placeholder="e.g. PGNU/PO2627/0442" autofocus>
  </div>

  <template v-if="poCodeInput.trim()">
    <div v-if="pendingItems === null"></div>
    <div v-else-if="!pendingItems.length" class="empty-state" style="margin-top: 14px;">
      No PO found with that code, or every SKU on it is already fully dispatched/received.
    </div>
    <template v-else>
      <div class="table-card" style="margin-top: 18px;"><div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>SKU</th><th>Item</th><th class="num">Pending</th>
              <th class="num">Qty to dispatch</th><th>Courier</th><th>AWB / Tracking ID</th><th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="it in pendingItems" :key="it.item_sku">
              <td class="mono">{{ it.item_sku }}</td>
              <td>{{ it.item_name || "–" }}</td>
              <td class="num mono">{{ fmtNum(it.pending_quantity) }}</td>
              <td class="num">
                <input
                  v-model="qtyInputs[it.item_sku]" type="number" min="0" :max="it.pending_quantity"
                  style="width: 90px;" :disabled="rowDone[it.item_sku]"
                >
              </td>
              <td>
                <select v-model="courierInputs[it.item_sku]" :disabled="rowDone[it.item_sku]">
                  <option v-for="c in COURIER_OPTIONS" :key="c.value" :value="c.value">{{ c.label }}</option>
                </select>
              </td>
              <td>
                <input v-model="awbInputs[it.item_sku]" type="text" placeholder="Required" style="width: 140px;" :disabled="rowDone[it.item_sku]">
              </td>
              <td>
                <span v-if="rowDone[it.item_sku]" class="chip chip-good">Dispatched</span>
                <div v-if="rowErrors[it.item_sku]" class="form-error" style="font-size: 0.72rem;">{{ rowErrors[it.item_sku] }}</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div></div>

      <div v-if="formError" class="form-error" style="margin-top: 12px;">{{ formError }}</div>
      <button class="link-btn-inline" style="margin-top: 16px;" :disabled="submitting" @click="handleSubmit">
        {{ submitting ? "Working…" : "Confirm Manual Dispatch" }}
      </button>
    </template>
  </template>
</template>
