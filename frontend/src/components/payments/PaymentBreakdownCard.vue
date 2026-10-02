<script setup>
// One breakdown card (Outstanding by age / Reconciliation status / Payment status): a bar
// per bucket, value + invoice count, bar length = share of the card's total value. Click a
// bucket to list the invoices behind it (emitted up so only one list is open per page).
import { computed } from "vue";
import { fmtMoney, fmtMoneyCompact } from "../../format.js";

const props = defineProps({
  title: { type: String, required: true },
  sub: { type: String, default: "" },
  buckets: { type: Array, required: true }, // summarise()'s ageing/recon/payment
  selected: { type: String, default: null }, // key of the open bucket on this card
  note: { type: String, default: "" },
});
const emit = defineEmits(["pick"]);

const total = computed(() => props.buckets.reduce((s, b) => s + b.amount, 0));
const pct = (b) => (total.value > 0 ? Math.max(b.amount > 0 ? 1.5 : 0, (b.amount / total.value) * 100) : 0);
</script>

<template>
  <section class="table-card">
    <h3 class="card-caption">{{ title }}<span v-if="sub" class="pay-card-sub">{{ sub }}</span></h3>
    <ul class="pay-bars">
      <li v-for="b in buckets" :key="b.key">
        <button
          type="button" class="pay-bar" :class="{ sel: selected === b.key }" :disabled="!b.count"
          :title="`${fmtMoney(b.amount)} · ${b.count} invoice${b.count === 1 ? '' : 's'}`"
          @click="emit('pick', b.key)"
        >
          <div class="pay-bar-head">
            <span class="pay-bar-label">{{ b.label }}</span>
            <span class="pay-bar-fig" :class="b.count ? b.cls : 'muted'">{{ b.count ? fmtMoneyCompact(b.amount) : "–" }}<small>{{ b.count }}</small></span>
          </div>
          <div class="meter-track"><div class="meter-fill" :style="{ width: pct(b) + '%' }"></div></div>
        </button>
      </li>
    </ul>
    <div v-if="note" class="pay-note">{{ note }}</div>
  </section>
</template>
