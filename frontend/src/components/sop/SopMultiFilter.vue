<script setup>
// Checkbox multi-select dropdown for S&OP table filter rows -- same behaviour as
// Spares Inventory's category picker. modelValue: null = All, otherwise a Set
// of ticked values. The panel is position:fixed to the button because
// .table-card clips overflow; it closes on outside click, Escape or page scroll.
import { ref, computed, watch, onMounted, onUnmounted } from "vue";

const props = defineProps({
  options: { type: Array, required: true },
  modelValue: { type: Set, default: null },
  labelFn: { type: Function, default: (v) => v },
});
const emit = defineEmits(["update:modelValue"]);

const open = ref(false);
const el = ref(null);
const panelStyle = ref({});

function toggleOpen() {
  const r = el.value?.getBoundingClientRect();
  if (r) panelStyle.value = { top: `${r.bottom + 4}px`, left: `${r.left}px` };
  open.value = !open.value;
}

// Drop ticked values that no longer exist after a data refresh.
watch(() => props.options, (opts) => {
  if (!props.modelValue) return;
  const live = new Set(opts);
  const kept = new Set([...props.modelValue].filter((v) => live.has(v)));
  if (kept.size !== props.modelValue.size) emit("update:modelValue", kept.size ? kept : null);
});

const isChecked = (v) => !props.modelValue || props.modelValue.has(v);
function toggle(v) {
  const cur = props.modelValue ? new Set(props.modelValue) : new Set(props.options);
  cur.has(v) ? cur.delete(v) : cur.add(v);
  emit("update:modelValue", cur.size === props.options.length ? null : cur);
}
const toggleAll = () => emit("update:modelValue", props.modelValue ? null : new Set());

const buttonLabel = computed(() => {
  const sel = props.modelValue;
  if (!sel) return "All";
  if (!sel.size) return "None";
  if (sel.size === 1) return props.labelFn([...sel][0]);
  return `${sel.size} selected`;
});

const onDocDown = (e) => { if (open.value && el.value && !el.value.contains(e.target)) open.value = false; };
const onDocKey = (e) => { if (e.key === "Escape") open.value = false; };
const onDocScroll = (e) => { if (open.value && !el.value?.contains(e.target)) open.value = false; };
onMounted(() => { document.addEventListener("mousedown", onDocDown); document.addEventListener("keydown", onDocKey); window.addEventListener("scroll", onDocScroll, true); });
onUnmounted(() => { document.removeEventListener("mousedown", onDocDown); document.removeEventListener("keydown", onDocKey); window.removeEventListener("scroll", onDocScroll, true); });
</script>

<template>
  <div ref="el" class="sop-multi">
    <button type="button" class="sop-multi-btn" @click="toggleOpen">{{ buttonLabel }} ▾</button>
    <div v-if="open" class="sop-multi-panel" :style="panelStyle">
      <label class="sop-multi-all">
        <input type="checkbox" :checked="!modelValue" :indeterminate.prop="!!modelValue && modelValue.size > 0" @change="toggleAll" /> All
      </label>
      <label v-for="v in options" :key="v">
        <input type="checkbox" :checked="isChecked(v)" @change="toggle(v)" /> {{ labelFn(v) }}
      </label>
    </div>
  </div>
</template>

<style scoped>
.sop-multi { position: relative; }
/* Matches .filter-row select in shared.css. */
.sop-multi-btn {
  width: 100%; min-width: 70px; text-align: left; cursor: pointer; white-space: nowrap; font-family: inherit; font-size: 0.76rem;
  padding: 4px 6px; border: 1px solid var(--line); border-radius: 5px; background: var(--surface); color: var(--ink);
}
.sop-multi-panel {
  position: fixed; z-index: 60; min-width: 200px; max-height: 320px; overflow-y: auto;
  padding: 6px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 6px 20px rgba(0, 0, 0, 0.12);
}
.sop-multi-panel label { display: flex; align-items: center; gap: 7px; padding: 4px 6px; border-radius: 5px; font-size: 0.78rem; font-weight: 400; cursor: pointer; white-space: nowrap; }
/* .filter-row input is width:100% -- undo that for the checkboxes. */
.sop-multi-panel input { width: auto; min-width: 0; margin: 0; }
.sop-multi-panel label:hover { background: var(--line); }
.sop-multi-panel .sop-multi-all { font-weight: 600; border-bottom: 1px solid var(--line); border-radius: 0; margin-bottom: 3px; }
</style>
