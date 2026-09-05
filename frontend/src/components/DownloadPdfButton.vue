<script setup>
import { computed } from "vue";
import { usePdfDownload } from "../composables/usePdfDownload.js";
import downloadIcon from "../assets/icons/download.png";

const props = defineProps({
  poCode: { type: String, required: true },
});

const { downloadingCodes, downloadPoPdf } = usePdfDownload();
const isDownloading = computed(() => downloadingCodes.has(props.poCode));
</script>

<template>
  <button
    class="icon-btn" :disabled="isDownloading"
    :title="isDownloading ? 'Fetching PDF…' : 'Download PDF'"
    :aria-label="isDownloading ? 'Fetching PDF…' : 'Download PDF'"
    @click.stop="downloadPoPdf(poCode)"
  >
    <img :src="downloadIcon" alt="" class="icon-mono">
  </button>
</template>
