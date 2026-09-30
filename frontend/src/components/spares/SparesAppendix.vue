<script setup>
// Appendix: the decision layer. One row per SKU (sheet SKUs + anything stocked at a warehouse);
// one Ongoing / Obsolete / NA status per selected warehouse, and one vendor + category per SKU.
// Defaults follow the sheet (Discontinued -> Obsolete, other sheet SKUs -> Ongoing, rest -> NA) until
// someone edits a cell; an edited cell shows a dot and can be reset back to the sheet default.
import { ref, computed } from "vue";
import { FACILITIES, STATUSES } from "../../composables/useSparesData.js";
import SparesVendorPicker from "./SparesVendorPicker.vue";

const props = defineProps({ store: { type: Object, required: true } });
const s = props.store;

const q = ref("");
const selected = ref(FACILITIES.map((f) => f.code));
const statusFilter = ref("All");
const sourceFilter = ref("All");

const allOn = computed(() => selected.value.length === FACILITIES.length);
function toggleAll() { selected.value = allOn.value ? [] : FACILITIES.map((f) => f.code); }
function toggle(code) {
  selected.value = selected.value.includes(code) ? selected.value.filter((c) => c !== code) : FACILITIES.filter((f) => f.code === code || selected.value.includes(f.code)).map((f) => f.code);
}
const cols = computed(() => FACILITIES.filter((f) => selected.value.includes(f.code)));

const rows = computed(() => {
  const needle = q.value.trim().toLowerCase();
  return s.allSkus.value.filter((sku) => {
    if (needle && !sku.toLowerCase().includes(needle)) return false;
    const inSheet = s.masterBySku.value.has(sku);
    if (sourceFilter.value === "sheet" && !inSheet) return false;
    if (sourceFilter.value === "other" && inSheet) return false;
    if (statusFilter.value !== "All" && !cols.value.some((f) => s.statusOf(sku, f.code) === statusFilter.value)) return false;
    return true;
  });
});

const counts = computed(() => {
  const c = { Ongoing: 0, Obsolete: 0, NA: 0 };
  for (const sku of rows.value) for (const f of cols.value) c[s.statusOf(sku, f.code)]++;
  return c;
});

function setAll(sku, ev) {
  const v = ev.target.value;
  ev.target.value = "";
  if (!v) return;
  for (const f of cols.value) if (s.statusOf(sku, f.code) !== v || !s.isEdited(sku, f.code)) s.setStatus(sku, f.code, v);
}
function commitVendor(sku, v) {
  v = (v || "").trim();
  if (v === s.vendorOf(sku)) return;
  if (!v || v === s.sheetVendor(sku)) s.resetVendor(sku);
  else s.setVendor(sku, v);
}
function commitCategory(sku, v) {
  v = (v || "").trim();
  if (v === s.categoryOf(sku)) return;
  if (!v || v === s.sheetCategory(sku)) s.resetCategory(sku);
  else s.setCategory(sku, v);
}
</script>

<template>
  <section class="table-card hc-view">
    <div class="sp-toolbar">
      <h3 class="card-caption" style="padding:0;border:none">Appendix</h3>
      <input v-model="q" class="sp-search" type="search" placeholder="Search SKU ID" />
      <select v-model="statusFilter" class="sp-select">
        <option value="All">All statuses</option>
        <option v-for="st in STATUSES" :key="st" :value="st">{{ st }}</option>
      </select>
      <select v-model="sourceFilter" class="sp-select">
        <option value="All">All SKUs</option>
        <option value="sheet">In Spares sheet</option>
        <option value="other">Warehouse only (not in sheet)</option>
      </select>
      <span class="grow"></span>
      <span class="sp-count"><b>{{ rows.length }}</b> SKUs ·
        <span class="sp-tag Ongoing">{{ counts.Ongoing }} Ongoing</span>
        <span class="sp-tag Obsolete">{{ counts.Obsolete }} Obsolete</span>
        <span class="sp-tag NA">{{ counts.NA }} NA</span>
      </span>
    </div>
    <div class="sp-toolbar">
      <div class="sp-chips">
        <button type="button" :class="{ on: allOn }" @click="toggleAll">All</button>
        <button v-for="f in FACILITIES" :key="f.code" type="button" :class="{ on: selected.includes(f.code) }" @click="toggle(f.code)">{{ f.label }}</button>
      </div>
      <span class="grow"></span>
      <span class="sp-legend"><span><span class="sp-edited"></span>edited</span><span>↺ reset to sheet default</span></span>
    </div>

    <div class="table-scroll">
      <table class="sp-table">
        <thead><tr>
          <th class="sp-sticky">SKU ID</th>
          <th>Category</th>
          <th>Vendor</th>
          <th v-for="f in cols" :key="f.code" class="c gl">{{ f.label }}</th>
          <th v-if="cols.length > 1" class="c gl">Set all</th>
        </tr></thead>
        <tbody>
          <tr v-for="sku in rows" :key="sku">
            <td class="sku sp-sticky">{{ sku }}</td>
            <td>
              <SparesVendorPicker noun="category" :value="s.categoryOf(sku)" :options="s.categoryOptions.value" :edited="s.categoryEdited(sku)"
                                  @commit="commitCategory(sku, $event)" @reset="s.resetCategory(sku)" />
            </td>
            <td>
              <SparesVendorPicker :value="s.vendorOf(sku)" :options="s.vendorOptions.value" :edited="s.vendorEdited(sku)"
                                  @commit="commitVendor(sku, $event)" @reset="s.resetVendor(sku)" />
            </td>
            <td v-for="f in cols" :key="f.code" class="c gl">
              <span class="sp-cell">
                <span v-if="s.isEdited(sku, f.code)" class="sp-edited" :title="'Edited'"></span>
                <select class="sp-status" :class="s.statusOf(sku, f.code)" :value="s.statusOf(sku, f.code)"
                        @change="s.setStatus(sku, f.code, $event.target.value)">
                  <option v-for="st in STATUSES" :key="st" :value="st">{{ st }}</option>
                </select>
                <button v-if="s.isEdited(sku, f.code)" type="button" class="sp-reset" title="Reset to sheet default" @click="s.resetStatus(sku, f.code)">↺</button>
              </span>
            </td>
            <td v-if="cols.length > 1" class="c gl">
              <select class="sp-status" @change="setAll(sku, $event)">
                <option value="">—</option>
                <option v-for="st in STATUSES" :key="st" :value="st">{{ st }}</option>
              </select>
            </td>
          </tr>
          <tr v-if="!rows.length"><td :colspan="cols.length + 4" class="dim">No SKUs match these filters.</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
