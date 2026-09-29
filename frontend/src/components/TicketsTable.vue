<script setup>
import { computed } from "vue";
import SummaryKpis from "./SummaryKpis.vue";
import { fmtDateOnly, ticketCategoryLabel, ticketStatusLabel, ticketStatusClass } from "../format.js";

const props = defineProps({
  rows: { type: Array, required: true },        // every ticket this login can see (RLS: all, for internal staff)
  vendorLabel: { type: Function, default: null }, // (code, rowName) => string
  onOpenTicket: { type: Function, required: true }, // (id) => void
});

const kpiTiles = computed(() => {
  const counts = { open: 0, in_progress: 0, resolved: 0 };
  for (const t of props.rows) counts[t.status] = (counts[t.status] || 0) + 1;
  return [
    { label: "Total tickets", value: props.rows.length },
    { label: "Open", value: counts.open, cls: counts.open > 0 ? "critical" : "" },
    { label: "In progress", value: counts.in_progress },
    { label: "Resolved", value: counts.resolved, cls: "good" },
  ];
});
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th>Vendor</th><th>Raised on</th><th>Category</th><th>PO code</th><th>Subject</th><th>Status</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!rows.length">
          <td colspan="6" class="empty-state">No tickets raised yet.</td>
        </tr>
        <tr v-for="t in rows" :key="t.id" class="clickable-row" @click="onOpenTicket(t.id)">
          <td>{{ vendorLabel ? vendorLabel(t.vendor_code, t.vendor_name) : (t.vendor_name || t.vendor_code) }}</td>
          <td class="mono">{{ fmtDateOnly(t.created_at) }}</td>
          <td>{{ ticketCategoryLabel(t.category) }}</td>
          <td class="mono">{{ t.po_code || "–" }}</td>
          <td>{{ t.subject }}</td>
          <td><span class="chip" :class="`chip-${ticketStatusClass(t.status)}`">{{ ticketStatusLabel(t.status) }}</span></td>
        </tr>
      </tbody>
    </table>
  </div></div>
</template>
