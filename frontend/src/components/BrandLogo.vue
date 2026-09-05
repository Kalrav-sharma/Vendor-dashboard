<script setup>
// Both logos are self-hosted PNGs cut from the source files in
// Desktop/Native brand theme -- black artwork on a transparent
// background, so the shared .brand-logo CSS rule can invert them to
// white for dark backgrounds without needing a second asset.
import nativeLogo from "../assets/logos/native-logo.png";
import ucLogo from "../assets/logos/uc-logo.png";

const props = defineProps({
  brand: { type: String, required: true }, // "native" | "uc"
});

const SRC = { native: nativeLogo, uc: ucLogo };
const ALT = { native: "Native", uc: "Urban Company" };
</script>

<template>
  <img :src="SRC[brand]" :alt="ALT[brand]" class="brand-logo">
</template>
