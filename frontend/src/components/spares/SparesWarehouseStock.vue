<script setup>
// Warehouse stock: every SKU held at one Uniware facility (Pataudi / Panchla separate), with
// good and bad units from the live inventory snapshot and its Appendix status.
import { ref, computed } from "vue";
import { FACILITIES, STATUSES } from "../../composables/useSparesData.js";

const props = defineProps({ store: { type: Object, required: true } });
const s = props.store;

const facility = ref(FACILITIES[0].code);
const q = ref("");
const statusFilter = ref("All");
const sortKey = ref("good");
const sortDir = ref(-1);

function sortBy(k) {
  if (sortKey.value === k) sortDir.value = -sortDir.value;
  else { sortKey.value = k; sortDir.value = k === "sku" || k === "category" || k === "status" ? 1 : -1; }
}
const arrow = (k) => (sortKey.value === k ? (sortDir.value > 0 ? " ▲" : " ▼") : "");

const allRows = computed(() =>
  s.allSkus.value
    .map((sku) => {
      const st = s.stockAt(sku, facility.value);
      return { sku, category: s.categoryOf(sku) || "–", status: s.statusOf(sku, facility.value), good: st.good, bad: st.bad, total: st.good + st.bad };
    })
    .filter((r) => r.total > 0),
);

const rows = computed(() => {
  const needle = q.value.trim().toLowerCase();
  const k = sortKey.value, d = sortDir.value;
  return allRows.value
    .filter((r) => (!needle || r.sku.toLowerCase().includes(needle)) && (statusFilter.value === "All" || r.status === statusFilter.value))
    .sort((a, b) => (typeof a[k] === "number" ? (a[k] - b[k]) * d : String(a[k]).localeCompare(String(b[k])) * d) || a.sku.localeCompare(b.sku));
});

const totals = computed(() => rows.value.reduce((t, r) => ({ good: t.good + r.good, bad: t.bad + r.bad }), { good: 0, bad: 0 }));
const fmt = (n) => Math.round(n).toLocaleString("en-IN");
</script>

<template>
  <section class="table-card hc-view">
    <div class="sp-toolbar">
      <h3 class="card-caption" style="padding:0;border:none">Warehouse stock</h3>
      <select v-model="facility" class="sp-select">
        <option v-for="f in FACILITIES" :key="f.code" :value="f.code">{{ f.label }} · {{ f.code }}</option>
      </select>
      <select v-model="statusFilter" class="sp-select">
        <option value="All">All statuses</option>
        <option v-for="st in STATUSES" :key="st" :value="st">{{ st }}</option>
      </select>
      <span class="grow"></span>
      <span class="sp-count"><b>{{ rows.length }}</b> SKUs · good <b>{{ fmt(totals.good) }}</b> · bad <b>{{ fmt(totals.bad) }}</b></span>
      <input v-model="q" class="sp-search" type="search" placeholder="Search SKU ID" />
    </div>
    <div class="table-scroll">
      <table class="sp-table">
        <thead><tr>
          <th class="sortable" @click="sortBy('sku')">SKU ID{{ arrow("sku") }}</th>
          <th class="sortable" @click="sortBy('category')">Category{{ arrow("category") }}</th>
          <th class="sortable" @click="sortBy('status')">Status{{ arrow("status") }}</th>
          <th class="num sortable" @click="sortBy('good')">Good{{ arrow("good") }}</th>
          <th class="num sortable" @click="sortBy('bad')">Bad{{ arrow("bad") }}</th>
          <th class="num sortable" @click="sortBy('total')">Total{{ arrow("total") }}</th>
        </tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.sku">
            <td class="sku">{{ r.sku }}</td>
            <td :class="{ dim: r.category === '–' }">{{ r.category }}</td>
            <td><span class="sp-tag" :class="r.status">{{ r.status }}</span></td>
            <td class="num hc-num">{{ fmt(r.good) }}</td>
            <td class="num hc-num" :class="{ dim: !r.bad }">{{ fmt(r.bad) }}</td>
            <td class="num hc-num"><b>{{ fmt(r.total) }}</b></td>
          </tr>
          <tr v-if="!rows.length"><td colspan="6" class="dim">No stock{{ q || statusFilter !== "All" ? " matching these filters" : "" }} at this warehouse.</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
