// Logistics Health Card › SLA & Demand Share. Reads the last ~7 promised-delivery weeks of
// sla_trend_weekly for both products: 'ro' (query 540408's base) and 'locks' (559060's base).
// Written from a VPN machine by ~/.claude/scripts/sla_portal/sync_sla_portal.js.
//
// The rows are per city_key (5 WH cities and 9 MFC cities individually, plus 'other'), so
// the Health Card's own tiers are formed here:
//   Top 5  = the 5 warehouse cities
//   Next 4 = the rest of the Top 9 (Chennai, Pune, Ahmedabad, Lucknow)
//   Other  = everything else
// Ratios are derived after summing, never averaged.
import { ref, computed, onMounted, onUnmounted } from "vue";
import { fetchAllRows } from "./sopPagedFetch.js";

const POLL_INTERVAL_MS = 5 * 60 * 1000;
// Next week's row appears once it "starts": enough early deliveries against next week's
// promise to be worth reading, not the odd 1-2 orders delivered a week ahead.
const NEXT_WEEK_MIN_ORDERS = 20;
const TOP5 = new Set(["city_mumbai_v2", "city_hyderabad_v2", "city_kolkata_v2", "city_delhi_v2", "city_bangalore_v2"]);
const NEXT4 = new Set(["city_chennai_v2", "city_pune_v2", "city_ahmedabad_v2", "city_lucknow_v2"]);
export const TIERS = [
  { key: "pan", label: "Pan India" },
  { key: "top5", label: "Top 5 cities", hint: "Mumbai, Delhi, Bangalore, Hyderabad, Kolkata" },
  { key: "next4", label: "Next 4 cities", hint: "Chennai, Pune, Ahmedabad, Lucknow" },
  { key: "other", label: "Other cities", hint: "All remaining cities" },
];
const tierOf = cityKey => (TOP5.has(cityKey) ? "top5" : NEXT4.has(cityKey) ? "next4" : "other");

function mondayOf(d) {
  const x = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  x.setUTCDate(x.getUTCDate() - ((x.getUTCDay() + 6) % 7));
  return x.toISOString().slice(0, 10);
}
const addDays = (ymd, n) => new Date(new Date(`${ymd}T00:00:00Z`).getTime() + n * 86400000).toISOString().slice(0, 10);

export function useHealthSlaData() {
  const rows = ref([]);
  const loadError = ref("");
  const lastSynced = ref(null);

  async function refresh() {
    const from = addDays(mondayOf(new Date()), -5 * 7);
    const { data, error } = await fetchAllRows("sla_trend_weekly", q => q.gte("week_start", from));
    if (error) { loadError.value = error.message; return; }
    loadError.value = "";
    rows.value = data;
    lastSynced.value = data.reduce((m, r) => (!m || r.synced_at > m ? r.synced_at : m), null);
  }

  // weeks(product) -> newest first: [next (once it has NEXT_WEEK_MIN_ORDERS)], current, 4 previous
  function weeks(product) {
    const cur = mondayOf(new Date());
    const wanted = [addDays(cur, 7), cur, ...[1, 2, 3, 4].map(i => addDays(cur, -7 * i))];
    return wanted.map(ws => {
      const rs = rows.value.filter(r => r.product === product && r.week_start === ws);
      const acc = Object.fromEntries(TIERS.map(t => [t.key, { orders: 0, tat_sum: 0, tat_n: 0 }]));
      rs.forEach(r => {
        for (const k of ["pan", tierOf(r.city_key)]) {
          acc[k].orders += Number(r.orders) || 0;
          acc[k].tat_sum += Number(r.tat_sum) || 0;
          acc[k].tat_n += Number(r.tat_n) || 0;
        }
      });
      const tiers = Object.fromEntries(TIERS.map(t => [t.key, {
        orders: acc[t.key].orders,
        sla: acc[t.key].tat_n ? acc[t.key].tat_sum / acc[t.key].tat_n : null,
        share: acc.pan.orders ? acc[t.key].orders / acc.pan.orders : null,
      }]));
      return {
        weekStart: ws,
        weekNo: rs[0]?.week_no ?? null,
        kind: ws > cur ? "next" : ws === cur ? "current" : "past",
        tiers,
      };
    }).filter(w => w.kind !== "next" || w.tiers.pan.orders >= NEXT_WEEK_MIN_ORDERS);
  }

  const ro = computed(() => weeks("ro"));
  const locks = computed(() => weeks("locks"));

  let timer = null;
  onMounted(() => { refresh(); timer = setInterval(refresh, POLL_INTERVAL_MS); });
  onUnmounted(() => clearInterval(timer));

  return { ro, locks, loadError, lastSynced, refresh };
}
