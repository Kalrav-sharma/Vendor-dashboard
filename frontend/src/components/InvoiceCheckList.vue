<script setup>
// The checks an uploaded invoice went through, ✅/❌ with the numbers, plus what to do --
// invoiceCheckBreakdown() in reconciliation.js. Shown in the "Why?" box on the Payments page
// (InvoiceCheckExplainer.vue) and inside the credit-note upload window.
import { computed } from "vue";
import { invoiceCheckBreakdown } from "../reconciliation.js";

const props = defineProps({ row: { type: Object, required: true } });
const b = computed(() => invoiceCheckBreakdown(props.row));
const ICON = { ok: "✓", fail: "✕", warn: "!", na: "–" };
</script>

<template>
  <ul class="icl-checks">
    <li v-for="c in b.checks" :key="c.label" :class="`icl-${c.state}`">
      <span class="icl-icon" aria-hidden="true">{{ ICON[c.state] }}</span>
      <div><b>{{ c.label }}</b><span>{{ c.detail }}</span></div>
    </li>
  </ul>
  <div v-if="b.actions.length" class="icl-actions">
    <b>What to do</b>
    <ul><li v-for="(a, i) in b.actions" :key="i">{{ a }}</li></ul>
  </div>
</template>
