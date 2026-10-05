// Logistics Health Card › SLA Adherence for Spares and Refresh. Reads health_sla_kit_weekly,
// one row per (product, order week, tier) where tier is pan / top5 / next4 / other (city tiers
// as in SLA & Demand Share). Delivered orders only. Written from a VPN machine by
// ~/.claude/scripts/sla_portal/sync_sla_portal.js:
//   Spares:  Jarvis 578703 -> on-time (orders, on_time) and days (tat_sum / tat_n, DELIVERY_TAT)
//   Refresh: Jarvis 579905 -> on-time; Jarvis 508892 "Refresh Kit Orders" -> days (DELIVERY_DAYS)
// Ratios are derived after summing, never averaged.
import { ref, computed, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

const POLL_INTERVAL_MS = 5 * 60 * 1000;
export const KIT_TIERS = [
  { key: "top5", label: "Top 5", hint: "Mumbai, Delhi, Bangalore, Hyderabad, Kolkata" },
  { key: "next4", label: "Next 4", hint: "Chennai, Pune, Ahmedabad, Lucknow" },
  { key: "other", label: "Other", hint: "All remaining cities" },
];

function mondayOf(d) {
  const x = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  x.setUTCDate(x.getUTCDate() - ((x.getUTCDay() + 6) % 7));
  return x.toISOString().slice(0, 10);
}
const addDays = (ymd, n) => new Date(new Date(`${ymd}T00:00:00Z`).getTime() + n * 86400000).toISOString().slice(0, 10);

export function useHealthKitSlaData() {
  const rows = ref([]);
  const loadError = ref("");
  const lastSynced = ref(null);

  async function refresh() {
    const { data, error } = await supabase.from("health_sla_kit_weekly").select("*")
      .gte("week_start", addDays(mondayOf(new Date()), -4 * 7));
    if (error) { loadError.value = error.message; return; }
    loadError.value = "";
    rows.value = data;
    lastSynced.value = data.reduce((m, r) => (!m || r.synced_at > m ? r.synced_at : m), null);
  }

  // newest first: current order week, then the 4 previous ones. Each tier (and pan) is
  // { total, onTime, tatSum, tatN } -- the shape HealthSlaAdherence's helpers read.
  function weeks(product) {
    const cur = mondayOf(new Date());
    return [0, 1, 2, 3, 4].map(i => {
      const ws = addDays(cur, -7 * i);
      const rs = rows.value.filter(r => r.product === product && r.week_start === ws);
      const tiers = Object.fromEntries(["pan", ...KIT_TIERS.map(t => t.key)].map(k => {
        const r = rs.find(x => x.tier === k);
        return [k, r ? { total: Number(r.orders) || 0, onTime: Number(r.on_time) || 0, tatSum: Number(r.tat_sum) || 0, tatN: Number(r.tat_n) || 0 } : null];
      }));
      return { weekStart: ws, weekNo: rs[0]?.week_no ?? null, kind: i === 0 ? "current" : "past", tiers };
    });
  }
  const spares = computed(() => weeks("spares"));
  const refreshKit = computed(() => weeks("refresh"));

  let timer = null;
  onMounted(() => { refresh(); timer = setInterval(refresh, POLL_INTERVAL_MS); });
  onUnmounted(() => clearInterval(timer));

  return { spares, refreshKit, loadError, lastSynced, refresh };
}
