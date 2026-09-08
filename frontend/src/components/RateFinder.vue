<script setup>
import { ref, reactive, computed, watch, onMounted } from "vue";
import { supabase } from "../supabaseClient.js";
import { resolveFunctionError } from "../functionError.js";
import { useRateCard } from "../composables/useRateCard.js";
import { useVendorContacts } from "../composables/useVendorContacts.js";
import CustomSelect from "./CustomSelect.vue";

const { rows, refresh: refreshRateCard, origins, destinations, truckSizes, findLane } = useRateCard();
const { contacts, refresh: refreshContacts, contactFor, saveContact, deleteContact } = useVendorContacts();

onMounted(async () => {
  await refreshRateCard();
  await refreshContacts();
});

function toOptions(values) {
  return values.map(v => ({ value: v, label: v }));
}

const query = reactive({ pickup: "", drop: "", truck: "" });
const selectedVendor = ref(null);

const lane = computed(() => {
  if (!query.pickup || !query.drop || !query.truck) return null;
  return findLane(query.pickup, query.drop, query.truck);
});

// Every vendor the rate card has ever seen (union across all lanes, not
// just this one) -- what the "Vendor WhatsApp contacts" panel manages.
const allVendorNames = computed(() => {
  const names = new Set();
  for (const row of rows.value) {
    for (const v of Object.keys(row.vendor_rates || {})) names.add(v);
  }
  return [...names].sort();
});

const vendorRatesSorted = computed(() => {
  if (!lane.value) return [];
  return Object.entries(lane.value.vendor_rates || {})
    .map(([vendor, price]) => ({ vendor, price }))
    .sort((a, b) => {
      if (a.price == null && b.price == null) return a.vendor.localeCompare(b.vendor);
      if (a.price == null) return 1;
      if (b.price == null) return -1;
      return a.price - b.price;
    });
});

// Default the selection to the cheapest vendor every time the lane changes.
watch(lane, (l) => { selectedVendor.value = l?.cheapest_vendor || null; });

function fmtRate(price) {
  return price == null ? "Not quoted" : `₹${price.toLocaleString("en-IN")}`;
}

// --- Send Intent ---
const sending = ref(false);
const sendError = ref("");
const sendSuccess = ref("");

async function handleSendIntent() {
  sendError.value = ""; sendSuccess.value = "";
  const picked = vendorRatesSorted.value.find(v => v.vendor === selectedVendor.value);
  if (!picked || picked.price == null) return;

  sending.value = true;
  const { data, error } = await supabase.functions.invoke("send-vendor-intent", {
    body: {
      vendor_name: picked.vendor, pickup_city: query.pickup, drop_city: query.drop,
      truck_size: query.truck, rate: picked.price,
    },
  });
  sending.value = false;

  if (error || data?.error) {
    sendError.value = await resolveFunctionError(data, error);
    return;
  }
  sendSuccess.value = `Intent sent to ${picked.vendor} on WhatsApp.`;
}

// --- Vendor WhatsApp contacts panel ---
const editingVendor = ref(null);
const editNumber = ref("");
const editName = ref("");
const contactError = ref("");
const contactSaving = ref(false);

function startEdit(vendorName) {
  const existing = contactFor(vendorName);
  editingVendor.value = vendorName;
  editNumber.value = existing?.whatsapp_number || "";
  editName.value = existing?.contact_name || "";
  contactError.value = "";
}

async function saveEdit() {
  contactError.value = "";
  contactSaving.value = true;
  const result = await saveContact(editingVendor.value, editNumber.value.trim(), editName.value.trim());
  contactSaving.value = false;
  if (!result.ok) { contactError.value = result.error; return; }
  editingVendor.value = null;
}

async function removeContact(vendorName) {
  await deleteContact(vendorName);
}
</script>

<template>
  <div class="panel">
    <h2>Find cheapest vendor</h2>
    <div v-if="!rows.length" class="empty-state">
      No rate card data yet -- run the "Sync Mid Mile rate card" GitHub Actions workflow (or ask Kalrav to).
    </div>
    <template v-else>
      <div class="panel-grid" style="grid-template-columns: repeat(3, 1fr);">
        <div class="field">
          <label>Pickup city</label>
          <CustomSelect v-model="query.pickup" :options="toOptions(origins)" />
        </div>
        <div class="field">
          <label>Drop city</label>
          <CustomSelect v-model="query.drop" :options="toOptions(destinations)" />
        </div>
        <div class="field">
          <label>Truck size</label>
          <CustomSelect v-model="query.truck" :options="toOptions(truckSizes)" />
        </div>
      </div>

      <div v-if="query.pickup && query.drop && query.truck">
        <div v-if="!lane" class="empty-state" style="padding: 16px 0;">
          No rate card coverage for {{ query.pickup }} → {{ query.drop }}, {{ query.truck }}.
        </div>
        <template v-else>
          <div class="table-card" style="margin-top: 14px;"><div class="table-scroll">
            <table>
              <thead><tr><th></th><th>Vendor</th><th class="num">Rate</th></tr></thead>
              <tbody>
                <tr v-for="v in vendorRatesSorted" :key="v.vendor">
                  <td class="col-tight">
                    <input type="radio" :value="v.vendor" v-model="selectedVendor" :disabled="v.price == null">
                  </td>
                  <td>
                    {{ v.vendor }}
                    <span v-if="v.vendor === lane.cheapest_vendor" class="chip chip-good" style="margin-left: 8px;">Cheapest</span>
                  </td>
                  <td class="num mono">{{ fmtRate(v.price) }}</td>
                </tr>
              </tbody>
            </table>
          </div></div>

          <div v-if="sendError" class="form-error" style="margin-top: 14px;">{{ sendError }}</div>
          <div v-if="sendSuccess" class="form-success" style="margin-top: 14px;">{{ sendSuccess }}</div>
          <button
            class="primary-btn" style="width: auto; padding: 9px 20px; margin-top: 14px;"
            :disabled="!selectedVendor || sending"
            @click="handleSendIntent"
          >
            {{ sending ? "Sending…" : "Send Intent" }}
          </button>
        </template>
      </div>
    </template>
  </div>

  <div class="panel">
    <h2>Vendor WhatsApp contacts</h2>
    <div v-if="!allVendorNames.length" class="empty-state">No vendors in the rate card yet.</div>
    <div v-else class="table-card"><div class="table-scroll">
      <table>
        <thead><tr><th>Vendor</th><th>Contact name</th><th>WhatsApp number</th><th>Actions</th></tr></thead>
        <tbody>
          <tr v-for="name in allVendorNames" :key="name">
            <template v-if="editingVendor === name">
              <td>{{ name }}</td>
              <td><input v-model="editName" type="text" placeholder="e.g. Rohan Mehta"></td>
              <td><input v-model="editNumber" type="text" placeholder="e.g. 919876543210"></td>
              <td>
                <div style="display: flex; gap: 12px; align-items: center;">
                  <button class="link-btn-inline" :disabled="contactSaving" @click="saveEdit">
                    {{ contactSaving ? "Saving…" : "Save" }}
                  </button>
                  <button class="link-btn-inline" @click="editingVendor = null">Cancel</button>
                </div>
                <div v-if="contactError" class="form-error" style="margin: 6px 0 0;">{{ contactError }}</div>
              </td>
            </template>
            <template v-else>
              <td>{{ name }}</td>
              <td>{{ contactFor(name)?.contact_name || "–" }}</td>
              <td class="mono">{{ contactFor(name)?.whatsapp_number || "–" }}</td>
              <td>
                <div style="display: flex; gap: 12px; align-items: center;">
                  <button class="link-btn-inline" @click="startEdit(name)">
                    {{ contactFor(name) ? "Edit" : "Add number" }}
                  </button>
                  <button v-if="contactFor(name)" class="link-btn-inline" style="color: var(--critical);" @click="removeContact(name)">
                    Remove
                  </button>
                </div>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div></div>
  </div>
</template>
