// Logistics Health Card › Delayed Orders. Reads health_delay_weekly, one row per
// (product, order week) with MUTUALLY EXCLUSIVE delay bands: d3 = 4–5 days past promise,
// d5 = 6–10, d10 = 11–15, d15 = 16+. Written from a VPN machine by
// ~/.claude/scripts/sla_portal/sync_sla_portal.js, which aggregates Jarvis 558955 (Spares) and
// 579905 (Refresh). Open orders past promise count; cancelled and RTO orders are excluded.
import { ref, computed, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

const POLL_INTERVAL_MS = 5 * 60 * 1000;

function mondayOf(d) {
  const x = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  x.setUTCDate(x.getUTCDate() - ((x.getUTCDay() + 6) % 7));
  return x.toISOString().slice(0, 10);
}
const addDays = (ymd, n) => new Date(new Date(`${ymd}T00:00:00Z`).getTime() + n * 86400000).toISOString().slice(0, 10);

export function useHealthDelayData() {
  const rows = ref([]);
  const loadError = ref("");
  const lastSynced = ref(null);

  async function refresh() {
    const { data, error } = await supabase.from("health_delay_weekly").select("*")
      .gte("week_start", addDays(mondayOf(new Date()), -4 * 7));
    if (error) { loadError.value = error.message; return; }
    loadError.value = "";
    rows.value = data;
    lastSynced.value = data.reduce((m, r) => (!m || r.synced_at > m ? r.synced_at : m), null);
  }

  // newest first: current order week, then the 4 previous ones
  function weeks(product) {
    const cur = mondayOf(new Date());
    return [0, 1, 2, 3, 4].map(i => {
      const ws = addDays(cur, -7 * i);
      const r = rows.value.find(x => x.product === product && x.week_start === ws);
      return { weekStart: ws, weekNo: r?.week_no ?? null, current: i === 0, orders: r?.orders ?? null, d3: r?.d3 ?? null, d5: r?.d5 ?? null, d10: r?.d10 ?? null, d15: r?.d15 ?? null };
    });
  }
  const spares = computed(() => weeks("spares"));
  const refreshKit = computed(() => weeks("refresh"));

  let timer = null;
  onMounted(() => { refresh(); timer = setInterval(refresh, POLL_INTERVAL_MS); });
  onUnmounted(() => clearInterval(timer));

  return { spares, refreshKit, loadError, lastSynced, refresh };
}
