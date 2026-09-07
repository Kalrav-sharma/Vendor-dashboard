<script setup>
// A native <select>'s closed box can be styled with CSS, but its OPEN
// options popup is an OS-native widget browsers only give partial (and
// inconsistent) CSS control over -- on macOS Chrome in dark mode it stays
// a semi-transparent system panel no matter what .field select says. This
// renders the whole thing (trigger + options list) as plain HTML so it
// matches the portal's theme exactly, popup included.
import { ref, computed, onMounted, onBeforeUnmount } from "vue";

const props = defineProps({
  modelValue: { type: String, required: true },
  options: { type: Array, required: true }, // [{ value, label }]
});
const emit = defineEmits(["update:modelValue"]);

const open = ref(false);
const rootEl = ref(null);

const selectedLabel = computed(() => props.options.find(o => o.value === props.modelValue)?.label ?? "");

function toggle() { open.value = !open.value; }
function choose(value) {
  emit("update:modelValue", value);
  open.value = false;
}

function handleClickOutside(e) {
  if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false;
}
function handleEscape(e) {
  if (e.key === "Escape") open.value = false;
}
onMounted(() => {
  document.addEventListener("click", handleClickOutside);
  document.addEventListener("keydown", handleEscape);
});
onBeforeUnmount(() => {
  document.removeEventListener("click", handleClickOutside);
  document.removeEventListener("keydown", handleEscape);
});
</script>

<template>
  <div ref="rootEl" class="custom-select">
    <button type="button" class="custom-select-trigger" :class="{ open }" @click.stop="toggle">
      <span>{{ selectedLabel }}</span>
      <span class="custom-select-chevron">▾</span>
    </button>
    <ul v-if="open" class="custom-select-options">
      <li
        v-for="opt in options" :key="opt.value"
        class="custom-select-option" :class="{ selected: opt.value === modelValue }"
        @click="choose(opt.value)"
      >
        {{ opt.label }}
      </li>
    </ul>
  </div>
</template>
