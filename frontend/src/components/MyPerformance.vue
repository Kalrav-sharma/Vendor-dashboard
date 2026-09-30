<script setup>
defineProps({
  // [{ key, label, value, cls, sublabel, meter? }] -- meter: { pct, target, lowerIsBetter? },
  // drawn as a track with a target tick when the metric is a percentage
  fulfillment: { type: Array, required: true },
  paymentHealth: { type: Array, required: true }, // same shape
});
</script>

<template>
  <div v-for="group in [{ title: 'Fulfillment', tiles: fulfillment }, { title: 'Payment health', tiles: paymentHealth }]"
       :key="group.title" class="panel">
    <h2>{{ group.title }}</h2>
    <div class="dash-scorecard">
      <div v-for="tile in group.tiles" :key="tile.key" class="dash-scorecard-tile">
        <div class="dash-scorecard-label">{{ tile.label }}</div>
        <div class="dash-scorecard-value" :class="tile.cls">{{ tile.value }}</div>
        <div v-if="tile.meter" class="v-meter" role="img"
             :aria-label="`${tile.label} ${tile.value}, target ${tile.meter.lowerIsBetter ? 'at most' : 'at least'} ${tile.meter.target}%`">
          <div class="v-meter-fill" :style="{ width: `${Math.min(tile.meter.pct, 100)}%` }"></div>
          <div class="v-meter-target" :style="{ left: `${tile.meter.target}%` }"></div>
        </div>
        <div v-if="tile.sublabel" class="dash-scorecard-sub" :class="tile.cls">{{ tile.sublabel }}</div>
      </div>
    </div>
  </div>
</template>
