<script setup>
// "Updated ..." line under a Health Card table title: one chip per data source feeding the table,
// each dated with its own last sync. Dot goes amber when the source is older than its staleH hours
// (or has never synced), so a table that quietly stopped refreshing is visible.
const props = defineProps({ items: { type: Array, default: () => [] } }); // [{ label, at, staleH }]
const stamp = t => (t ? new Date(t).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "not synced yet");
const stale = i => !i.at || (i.staleH && (Date.now() - new Date(i.at).getTime()) / 3600000 > i.staleH);
</script>

<template>
  <div v-if="props.items.length" class="hc-asof">
    <span v-for="i in props.items" :key="i.label" :class="{ stale: stale(i) }">{{ i.label }} {{ stamp(i.at) }}</span>
  </div>
</template>
