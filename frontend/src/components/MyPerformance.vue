<script setup>
defineProps({
  fulfillment: { type: Array, required: true },  // [{ key, label, value, cls, sublabel }]
  paymentHealth: { type: Array, required: true }, // same shape
});
</script>

<template>
  <div class="panel">
    <h2>Fulfillment</h2>
    <div class="dash-scorecard">
      <div v-for="tile in fulfillment" :key="tile.key" class="dash-scorecard-tile">
        <div class="dash-scorecard-value" :class="tile.cls">{{ tile.value }}</div>
        <div class="dash-scorecard-label">{{ tile.label }}</div>
        <div v-if="tile.sublabel" class="dash-scorecard-sub" :class="tile.cls">{{ tile.sublabel }}</div>
      </div>
    </div>
  </div>

  <div class="panel">
    <h2>Payment health</h2>
    <div class="dash-scorecard">
      <div v-for="tile in paymentHealth" :key="tile.key" class="dash-scorecard-tile">
        <div class="dash-scorecard-value" :class="tile.cls">{{ tile.value }}</div>
        <div class="dash-scorecard-label">{{ tile.label }}</div>
        <div v-if="tile.sublabel" class="dash-scorecard-sub" :class="tile.cls">{{ tile.sublabel }}</div>
      </div>
    </div>
  </div>
</template>
