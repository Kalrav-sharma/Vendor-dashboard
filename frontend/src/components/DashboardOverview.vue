<script setup>
import { computed } from "vue";
import StatusChip from "./StatusChip.vue";
import { fmtMoney } from "../format.js";

const props = defineProps({
  actions: { type: Array, required: true },   // [{ key, count, title, sub, cta, tone, nav, bucket? }] -- zero-count items are hidden
  kpis: { type: Array, required: true },      // [{ key, label, sublabel, value, colorVar, icon, nav, bucket? }]
  health: { type: Array, required: true },    // [{ key, label, value, cls?, sub, meter? }] -- meter: { pct, target }
  recentPos: { type: Array, required: true }, // [{ po_code, sku_count, facility, status, total_amount }]
  onOpenPo: { type: Function, required: true },
  onNavigate: { type: Function, required: true }, // (navId, bucket?) => void
});

const openActions = computed(() => props.actions.filter((a) => a.count > 0));
</script>

<template>
  <div class="v-dash-top">
    <section class="panel v-attention">
      <div class="v-panel-head">
        <h2>Needs your attention</h2>
        <span v-if="openActions.length" class="muted-text">Pick one to jump straight to it</span>
      </div>
      <div v-if="!openActions.length" class="v-allclear">
        <span class="v-allclear-icon" aria-hidden="true">
          <svg viewBox="0 0 20 20"><path d="M5.5 10.5 8.5 13.5 14.5 7" /></svg>
        </span>
        <div>
          <b>You're all caught up</b>
          <div class="muted-text">No invoices, credit notes or dispatches are waiting on you.</div>
        </div>
      </div>
      <ul v-else class="v-action-list">
        <li v-for="a in openActions" :key="a.key">
          <button type="button" class="v-action" :class="`tone-${a.tone}`" @click="onNavigate(a.nav, a.bucket)">
            <span class="v-action-count">{{ a.count }}</span>
            <span class="v-action-text">
              <b>{{ a.title }}</b>
              <span>{{ a.sub }}</span>
            </span>
            <span class="v-action-cta">
              {{ a.cta }}
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5l5 5-5 5" /></svg>
            </span>
          </button>
        </li>
      </ul>
    </section>

    <section class="panel v-health">
      <h2>Delivery health</h2>
      <div v-for="m in health" :key="m.key" class="v-health-metric">
        <div class="v-health-row">
          <span>{{ m.label }}</span>
          <b class="mono" :class="m.cls">{{ m.value }}</b>
        </div>
        <div v-if="m.meter" class="v-meter" role="img" :aria-label="`${m.label} ${m.value}, target ${m.meter.target}%`">
          <div class="v-meter-fill" :style="{ width: `${Math.min(m.meter.pct, 100)}%` }"></div>
          <div class="v-meter-target" :style="{ left: `${m.meter.target}%` }"></div>
        </div>
        <div class="muted-text">{{ m.sub }}</div>
      </div>
      <button type="button" class="v-text-btn" @click="onNavigate('my-performance')">
        See full scorecard
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5l5 5-5 5" /></svg>
      </button>
    </section>
  </div>

  <div class="dash-kpis">
    <button
      v-for="tile in kpis" :key="tile.key" type="button" class="dash-kpi"
      :style="{ '--tile-color': `var(${tile.colorVar})` }" @click="onNavigate(tile.nav, tile.bucket)"
    >
      <div class="dash-kpi-head">
        <span class="dash-kpi-label">{{ tile.label }}</span>
        <span class="dash-kpi-icon" v-html="tile.icon"></span>
      </div>
      <div class="dash-kpi-value">{{ tile.value }}</div>
      <div class="dash-kpi-sub">{{ tile.sublabel }}</div>
    </button>
  </div>

  <div class="table-card">
    <div class="v-card-head">
      <h2>Recent purchase orders</h2>
      <button type="button" class="v-text-btn" @click="onNavigate('po-tracking', 'all')">
        View all
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M8 5l5 5-5 5" /></svg>
      </button>
    </div>
    <div v-if="!recentPos.length" class="empty-state">No purchase orders yet. New POs from Native appear here automatically.</div>
    <div v-else class="table-scroll">
      <table class="table-fluid">
        <thead>
          <tr><th>PO code</th><th>Facility</th><th>SKUs</th><th>Status</th><th class="num">PO value</th></tr>
        </thead>
        <tbody>
          <tr v-for="po in recentPos" :key="po.po_code" class="clickable-row" tabindex="0"
              @click="onOpenPo(po.po_code)" @keydown.enter="onOpenPo(po.po_code)">
            <td class="mono v-po-code">{{ po.po_code }}</td>
            <td class="fac-code">{{ po.facility || "–" }}</td>
            <td class="mono">{{ po.sku_count }}</td>
            <td><StatusChip :status="po.status" /></td>
            <td class="num mono">{{ fmtMoney(po.total_amount) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
