// Spares › Spares Delivery (was Health Card › Delayed Orders). Reads health_delay_weekly, one row
// per (product, order week) with MUTUALLY EXCLUSIVE delay bands: d3 = 4–5 days past promise,
// d5 = 6–10, d10 = 11–15, d15 = 16+. Written from a VPN machine by
// ~/.claude/scripts/sla_portal/sync_sla_portal.js, which aggregates Jarvis 578703 (Spares) and
// 579905 (Refresh). Only orders in transit with the LSP (verified dispatched + still moving in
// live Uniware) are banded; not-yet-dispatched, delivered, cancelled, RTO and lost orders are
// excluded. `orders` stays every live order of the week (% base).
// The same sync writes the delayed orders themselves to health_delay_orders (CSV download).
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

  // Order-level rows behind the counts (health_delay_orders), both products, the 5 weeks shown.
  // Fetched on demand for the CSV download, not polled.
  async function fetchDelayedOrders() {
    const out = [];
    const from = addDays(mondayOf(new Date()), -4 * 7);
    for (let i = 0; ; i += 1000) {
      const { data, error } = await supabase.from("health_delay_orders").select("*")
        .gte("week_start", from)
        .order("product").order("week_start", { ascending: false }).order("delay_days", { ascending: false }).order("order_code")
        .range(i, i + 999);
      if (error) throw new Error(error.message);
      out.push(...data);
      if (data.length < 1000) return out;
    }
  }

  let timer = null;
  onMounted(() => { refresh(); timer = setInterval(refresh, POLL_INTERVAL_MS); });
  onUnmounted(() => clearInterval(timer));

  return { spares, refreshKit, loadError, lastSynced, refresh, fetchDelayedOrders };
}
