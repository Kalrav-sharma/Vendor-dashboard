// SLA › Trends › On-Time Delivery for Spares and Refresh, by partner type (UC Partner,
// Sterling Partner, Sterling Lite Partner). Reads sla_partner_otd_weekly, one row per
// (product, order week, partner), written from a VPN machine by
// ~/.claude/scripts/sla_portal/sync_sla_portal.js from Jarvis 558955 (Spares) and 579905
// (Refresh). Counts are summed into a period first and the ratio taken after, never averaged.
import { ref, computed, onMounted, onUnmounted } from "vue";
import { fetchAllRows } from "./sopPagedFetch.js";
import { mondayOf, weekLabel, monthLabel } from "./useSlaTrendsData.js";

const POLL_INTERVAL_MS = 60 * 1000;
const WEEKS_SHOWN = 16;
export const PARTNERS = [
  { key: "uc", label: "UC Partner" },
  { key: "sterling", label: "Sterling Partner" },
  { key: "sterling_lite", label: "Sterling Lite Partner" },
];

export function usePartnerOtdData() {
  const rows = ref([]);
  const loadError = ref("");

  async function refresh() {
    const { data, error } = await fetchAllRows("sla_partner_otd_weekly");
    if (error) { loadError.value = error.message; return; }
    loadError.value = "";
    rows.value = data;
  }

  // -> [{ key, label, partial, byPartner: { uc: {delivered, on_time}, ... } }]
  function periods(product, grain) {
    const thisMonday = mondayOf(new Date());
    const thisMonth = thisMonday.slice(0, 7) + "-01";
    const byKey = new Map();
    for (const r of rows.value) {
      if (r.product !== product || r.week_start > thisMonday) continue;
      const key = grain === "week" ? r.week_start : r.week_start.slice(0, 7) + "-01";
      if (!byKey.has(key)) byKey.set(key, { key, weekNo: r.week_no, byPartner: Object.fromEntries(PARTNERS.map(p => [p.key, { delivered: 0, on_time: 0 }])) });
      const acc = byKey.get(key).byPartner[r.partner];
      if (acc) { acc.delivered += Number(r.delivered) || 0; acc.on_time += Number(r.on_time) || 0; }
    }
    let list = [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key));
    if (grain === "week") list = list.slice(-WEEKS_SHOWN);
    return list.map(p => ({
      ...p,
      partial: grain === "week" ? p.key === thisMonday : p.key === thisMonth,
      label: grain === "week" ? weekLabel(p.key, p.weekNo) : monthLabel(p.key),
    }));
  }

  const spares = { week: computed(() => periods("spares", "week")), month: computed(() => periods("spares", "month")) };
  const refreshKit = { week: computed(() => periods("refresh", "week")), month: computed(() => periods("refresh", "month")) };

  let timer = null;
  onMounted(() => { refresh(); timer = setInterval(refresh, POLL_INTERVAL_MS); });
  onUnmounted(() => clearInterval(timer));

  return { spares, refreshKit, loadError };
}
