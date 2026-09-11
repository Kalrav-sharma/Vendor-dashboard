<script setup>
// Shared "insights" tile row for every data-list section (PO Tracking,
// SKU Level Data, Dispatch Planning, Payment Dashboard) -- each table
// computes its own tiles from its own `rows` prop (already filtered/scoped
// the same way the table itself is) and just hands them here to render,
// so the KPI counts always match whatever's actually on screen.
defineProps({
  tiles: { type: Array, required: true }, // [{ label, value, cls? }] -- cls: "good"|"critical" tints the value
});
</script>

<template>
  <div class="kpis" :style="{ '--kpi-cols': tiles.length }">
    <div v-for="t in tiles" :key="t.label" class="kpi">
      <div class="label">{{ t.label }}</div>
      <div class="value" :class="t.cls">{{ t.value }}</div>
    </div>
  </div>
</template>
