<script setup>
// Appendix vendor / category field (noun prop): type any name. Focus shows every known option; typing filters the list
// and offers "+ Add … as new …" when the text matches none. Commits on pick / Enter / blur,
// Esc reverts. An existing name typed in different casing snaps to its canonical spelling.
import { ref, computed, watch, onUnmounted } from "vue";

const props = defineProps({
  value: { type: String, default: "" },
  options: { type: Array, default: () => [] },
  edited: { type: Boolean, default: false },
  noun: { type: String, default: "vendor" },
});
const emit = defineEmits(["commit", "reset"]);

const text = ref(props.value);
const open = ref(false);
const typed = ref(false); // unfiltered list until the user actually types
const active = ref(-1);
const inputEl = ref(null);
const listStyle = ref({});
// The table scrolls horizontally, which would clip an absolute list -- anchor it fixed to the input
// and close it if anything scrolls underneath.
function place() {
  const r = inputEl.value?.getBoundingClientRect();
  if (!r) return;
  const below = window.innerHeight - r.bottom;
  listStyle.value = below > 240 || below > r.top
    ? { top: `${r.bottom + 2}px`, left: `${r.left}px`, minWidth: `${Math.max(r.width, 220)}px` }
    : { bottom: `${window.innerHeight - r.top + 2}px`, left: `${r.left}px`, minWidth: `${Math.max(r.width, 220)}px` };
}
const listEl = ref(null);
function onScroll(e) { if (open.value && e.target !== listEl.value) commit(text.value); }
// Only the open picker listens -- not one listener per Appendix row.
watch(open, (o) => (o ? window.addEventListener("scroll", onScroll, true) : window.removeEventListener("scroll", onScroll, true)));
onUnmounted(() => window.removeEventListener("scroll", onScroll, true));
watch(() => props.value, (v) => { if (!open.value) text.value = v; });

const exact = computed(() => props.options.find((o) => o.toLowerCase() === text.value.trim().toLowerCase()) || null);
const items = computed(() => {
  const t = text.value.trim().toLowerCase();
  const list = typed.value && t ? props.options.filter((o) => o.toLowerCase().includes(t)) : props.options;
  const out = list.map((v) => ({ v, label: v, isNew: false }));
  if (typed.value && t && !exact.value && t !== "na" && t !== "n/a") out.unshift({ v: text.value.trim(), label: `+ Add "${text.value.trim()}" as new ${props.noun}`, isNew: true });
  return out;
});

function onFocus() { place(); open.value = true; typed.value = false; active.value = -1; }
function onInput() { typed.value = true; open.value = true; active.value = items.value.length ? 0 : -1; }
function commit(v) {
  const val = (exact.value && v.trim().toLowerCase() === exact.value.toLowerCase()) ? exact.value : v.trim();
  open.value = false;
  typed.value = false;
  text.value = val || props.value;
  if (val !== props.value) emit("commit", val);
}
function pick(item) { commit(item.v); }
function onKey(e) {
  if (e.key === "ArrowDown") { e.preventDefault(); open.value = true; active.value = Math.min(items.value.length - 1, active.value + 1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); active.value = Math.max(0, active.value - 1); }
  else if (e.key === "Enter") { e.preventDefault(); commit(active.value >= 0 && items.value[active.value] ? items.value[active.value].v : text.value); e.target.blur(); }
  else if (e.key === "Escape") { text.value = props.value; open.value = false; e.target.blur(); }
}
// Delay so a click on a list item lands before the blur closes the list.
function onBlur() { setTimeout(() => { if (open.value) commit(text.value); }, 150); }
</script>

<template>
  <span class="sp-combo">
    <input ref="inputEl" v-model="text" class="sp-vendor" :class="{ edited }" :placeholder="noun.charAt(0).toUpperCase() + noun.slice(1)"
           @focus="onFocus" @input="onInput" @keydown="onKey" @blur="onBlur" />
    <button v-if="edited" type="button" class="sp-reset" :title="`Reset to sheet ${noun}`" @mousedown.prevent @click="emit('reset')">↺</button>
    <ul v-if="open && items.length" ref="listEl" class="sp-combo-list" :style="listStyle">
      <li v-for="(it, i) in items" :key="(it.isNew ? '+' : '') + it.v" :class="{ on: i === active, add: it.isNew, cur: it.v === value }"
          @mousedown.prevent="pick(it)">{{ it.label }}</li>
    </ul>
  </span>
</template>
