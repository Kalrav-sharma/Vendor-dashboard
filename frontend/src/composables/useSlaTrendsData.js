// SLA › Trends. Reads sla_trend_weekly, one row per (promised-delivery week, city_key),
// written from a VPN machine by ~/.claude/scripts/sla_portal/sync_sla_portal.js because
// Jarvis can't be reached from GitHub Actions. See schema.sql's "SLA section" block.
//
// The table stores only raw counts and sums. Every ratio is derived here, AFTER summing
// rows into a period, so a monthly figure is a true volume-weighted value, never an
// average of weekly ratios.
//
// Same shape as the S&OP composables: fetch on mount, poll every 60s.
import { ref, computed, onMounted, onUnmounted } from "vue";
import { fetchAllRows } from "./sopPagedFetch.js";

const POLL_INTERVAL_MS = 60 * 1000;
const WEEKS_SHOWN = 16;

export const WH_CITIES = [
  { key: "city_delhi_v2", label: "Delhi" },
  { key: "city_bangalore_v2", label: "Bangalore" },
  { key: "city_kolkata_v2", label: "Kolkata" },
  { key: "city_mumbai_v2", label: "Mumbai" },
  { key: "city_hyderabad_v2", label: "Hyderabad" },
];
// Same 9-city MFC list as /late-delivery-rca's MFC_CITIES, so both show identical numbers.
export const MFC_CITIES = [
  "ahmedabad", "bhopal", "chandigarh", "chennai", "guwahati", "jaipur", "lucknow", "pune", "raipur",
].map(c => ({ key: `city_${c}_v2`, label: c[0].toUpperCase() + c.slice(1) }));

const FIELDS = ["orders", "tat_sum", "tat_n", "on_time", "ds_facility_orders", "sdd_lsp_orders", "sfx_mfc_orders", "sfx_orders"];
const zero = () => Object.fromEntries(FIELDS.map(f => [f, 0]));
const add = (acc, r) => { FIELDS.forEach(f => { acc[f] += Number(r[f]) || 0; }); return acc; };
export const ratio = (n, d) => (d ? n / d : null);

function mondayOf(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function weekLabel(ws, weekNo) {
  const [, m, d] = ws.split("-").map(Number);
  return { short: `W${weekNo}`, long: `Week ${weekNo} · ${d} ${MONTHS[m - 1]}` };
}
function monthLabel(ms) {
  const [y, m] = ms.split("-").map(Number);
  return { short: `${MONTHS[m - 1]} ${String(y).slice(2)}`, long: `${MONTHS[m - 1]} ${y}` };
}

export function useSlaTrendsData() {
  const rows = ref([]);
  const loadError = ref("");
  const lastSynced = ref(null);

  async function refresh() {
    // RO only: the table also holds Locks rows (product = 'locks') for the Health Card.
    const { data, error } = await fetchAllRows("sla_trend_weekly", q => q.eq("product", "ro"));
    if (error) { loadError.value = error.message; return; }
    loadError.value = "";
    rows.value = data;
    lastSynced.value = data.reduce((m, r) => (!m || r.synced_at > m ? r.synced_at : m), null);
  }

  // Builds the period series for "week" or "month" granularity. Each period gets:
  //   totals.{all,wh,mfc,other}: summed counts per city group
  //   city[city_key]: summed counts for that one city
  //   partial: the period hasn't finished yet (current week/month), so it's drawn dashed
  function periods(grain) {
    const thisMonday = mondayOf(new Date());
    const thisMonth = thisMonday.slice(0, 7) + "-01";
    // Rows keyed by a future promised-delivery week are orders already delivered early
    // against next week's promise. They're too thin to plot as a week of their own.
    const usable = rows.value.filter(r => r.week_start <= thisMonday);
    const byKey = new Map();
    for (const r of usable) {
      const key = grain === "week" ? r.week_start : r.week_start.slice(0, 7) + "-01";
      if (!byKey.has(key)) {
        byKey.set(key, {
          key, weekNo: r.week_no,
          totals: { all: zero(), wh: zero(), mfc: zero(), other: zero() },
          city: {},
        });
      }
      const p = byKey.get(key);
      add(p.totals.all, r);
      add(p.totals[r.city_group] || p.totals.other, r);
      p.city[r.city_key] = add(p.city[r.city_key] || zero(), r);
    }
    let list = [...byKey.values()].sort((a, b) => a.key.localeCompare(b.key));
    if (grain === "week") list = list.slice(-WEEKS_SHOWN);
    return list.map(p => ({
      ...p,
      partial: grain === "week" ? p.key === thisMonday : p.key === thisMonth,
      label: grain === "week" ? weekLabel(p.key, p.weekNo) : monthLabel(p.key),
    }));
  }

  const weekly = computed(() => periods("week"));
  const monthly = computed(() => periods("month"));

  let timer = null;
  onMounted(() => { refresh(); timer = setInterval(refresh, POLL_INTERVAL_MS); });
  onUnmounted(() => clearInterval(timer));

  return { rows, weekly, monthly, loadError, lastSynced, refresh };
}
