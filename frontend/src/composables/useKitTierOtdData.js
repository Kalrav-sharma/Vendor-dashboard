// SLA › Trends › On-Time Delivery for Spares, by city tier (Pan India, Top 5, Next 4, Other).
// Reads health_sla_kit_weekly, one row per (product, order week, tier), written from a VPN
// machine by ~/.claude/scripts/sla_portal/sync_sla_portal.js from Jarvis 578703 -- the same
// table behind the Health Card's Spares & Refresh SLA. Delivered orders only. Counts are summed
// into a period first and the ratio taken after, never averaged.
import { ref, computed, onMounted, onUnmounted } from "vue";
import { fetchAllRows } from "./sopPagedFetch.js";
import { mondayOf, weekLabel, monthLabel } from "./useSlaTrendsData.js";

const POLL_INTERVAL_MS = 60 * 1000;
const WEEKS_SHOWN = 16;
export const KIT_OTD_TIERS = [
  { key: "pan", label: "Pan India", color: "--sla-pan", width: 3 },
  { key: "top5", label: "Top 5 cities", color: "--sla-s1" },
  { key: "next4", label: "Next 4 cities", color: "--sla-s2" },
  { key: "other", label: "Other cities", color: "--sla-s3" },
];

export function useKitTierOtdData() {
  const rows = ref([]);
  const loadError = ref("");

  async function refresh() {
    const { data, error } = await fetchAllRows("health_sla_kit_weekly", q => q.eq("product", "spares"));
    if (error) { loadError.value = error.message; return; }
    loadError.value = "";
    rows.value = data;
  }

  // -> [{ key, label, partial, byTier: { pan: {orders, on_time}, top5: ..., ... } }]
  function periods(grain) {
    const thisMonday = mondayOf(new Date());
    const thisMonth = thisMonday.slice(0, 7) + "-01";
    const byKey = new Map();
    for (const r of rows.value) {
      if (r.week_start > thisMonday) continue;
      const key = grain === "week" ? r.week_start : r.week_start.slice(0, 7) + "-01";
      if (!byKey.has(key)) byKey.set(key, { key, weekNo: r.week_no, byTier: Object.fromEntries(KIT_OTD_TIERS.map(t => [t.key, { orders: 0, on_time: 0 }])) });
      const acc = byKey.get(key).byTier[r.tier];
      if (acc) { acc.orders += Number(r.orders) || 0; acc.on_time += Number(r.on_time) || 0; }
    }
    let list = [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key));
    if (grain === "week") list = list.slice(-WEEKS_SHOWN);
    return list.map(p => ({
      ...p,
      partial: grain === "week" ? p.key === thisMonday : p.key === thisMonth,
      label: grain === "week" ? weekLabel(p.key, p.weekNo) : monthLabel(p.key),
    }));
  }

  const spares = { week: computed(() => periods("week")), month: computed(() => periods("month")) };

  let timer = null;
  onMounted(() => { refresh(); timer = setInterval(refresh, POLL_INTERVAL_MS); });
  onUnmounted(() => clearInterval(timer));

  return { spares, loadError };
}
