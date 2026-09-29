<script setup>
defineProps({
  kpis: { type: Array, required: true },      // [{ key, label, sublabel, value, colorVar, icon }]
  scorecard: { type: Array, required: true }, // [{ key, label, sublabel, value, cls }]
  recentPos: { type: Array, required: true }, // [{ po_code, sku_count, facility }]
  onOpenPo: { type: Function, required: true },
});
</script>

<template>
  <div class="dash-kpis">
    <div v-for="tile in kpis" :key="tile.key" class="dash-kpi" :style="{ '--tile-color': `var(${tile.colorVar})` }">
      <div class="dash-kpi-head">
        <span class="dash-kpi-label">{{ tile.label }}</span>
        <span class="dash-kpi-icon" v-html="tile.icon"></span>
      </div>
      <div class="dash-kpi-value">{{ tile.value }}</div>
      <div class="dash-kpi-sub">{{ tile.sublabel }}</div>
    </div>
  </div>

  <div class="panel">
    <h2>Scorecard</h2>
    <div class="dash-scorecard">
      <div v-for="tile in scorecard" :key="tile.key" class="dash-scorecard-tile">
        <div class="dash-scorecard-value" :class="tile.cls">{{ tile.value }}</div>
        <div class="dash-scorecard-label">{{ tile.label }}</div>
        <div v-if="tile.sublabel" class="dash-scorecard-sub" :class="tile.cls">{{ tile.sublabel }}</div>
      </div>
    </div>
  </div>

  <div class="panel">
    <h2>Recent POs</h2>
    <div v-if="!recentPos.length" class="empty-state">No purchase orders yet.</div>
    <ul v-else class="dash-recent-pos">
      <li v-for="po in recentPos" :key="po.po_code">
        <button type="button" class="link-btn-inline" @click="onOpenPo(po.po_code)">{{ po.po_code }}</button>
        <span class="muted-text">{{ po.sku_count }} SKU{{ po.sku_count === 1 ? "" : "s" }}<template v-if="po.facility"> · {{ po.facility }}</template></span>
      </li>
    </ul>
  </div>
</template>
