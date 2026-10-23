// Spares > Projections (beta, internal only): per-vendor, per-SKU order projections for the next 3 months.
//   spares_projection <- scripts/sync_spares_projection.py (GitHub Actions; demand uploaded monthly from the
//   VPN laptop by sync_spares_forecast_demand.py, stock / in-transit / vendor refreshed from the spares sync).
// RLS limits both tables to admin / management / operations -- vendors and finance read zero rows.
import { ref, computed, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";
import { visibleInterval } from "./visibleInterval.js";

const POLL_INTERVAL_MS = 10 * 60 * 1000;
const PAGE = 1000;

export function useSparesProjection() {
  const rows = ref([]);
  const loadError = ref("");
  const loaded = ref(false);

  async function refresh() {
    const out = [];
    for (let from = 0; ; from += PAGE) {
      const { data, error } = await supabase.from("spares_projection").select("*")
        .order("sku").order("month_start").range(from, from + PAGE - 1);
      if (error) { loadError.value = error.message; loaded.value = true; return; }
      out.push(...data);
      if (data.length < PAGE) break;
    }
    loadError.value = "";
    rows.value = out;
    loaded.value = true;
  }

  const newest = (key) => computed(() => rows.value.reduce((t, r) => (r[key] && r[key] > t ? r[key] : t), "") || null);
  const computedAt = newest("computed_at");
  const forecastAsof = newest("forecast_asof");

  // The 3 projection months, oldest first, as { key: "2026-11-01", label: "Nov '26" }.
  const months = computed(() => {
    const keys = [...new Set(rows.value.map(r => r.month_start))].sort();
    return keys.map(k => ({
      key: k,
      label: new Date(`${k}T00:00:00Z`).toLocaleDateString("en-IN", { month: "short", year: "2-digit", timeZone: "UTC" }).replace(" ", " '"),
    }));
  });

  // [{ vendor, skus: [{ sku, qty: {monthKey: n}, total }], totals: {monthKey: n}, total }], biggest first,
  // "Unassigned" last. Vendors whose every SKU is fully covered (all zero) go to `covered`.
  const groups = computed(() => {
    const byVendor = new Map();
    for (const r of rows.value) {
      const v = r.vendor || "Unassigned";
      if (!byVendor.has(v)) byVendor.set(v, new Map());
      const bySku = byVendor.get(v);
      if (!bySku.has(r.sku)) bySku.set(r.sku, { sku: r.sku, qty: {}, total: 0 });
      const s = bySku.get(r.sku);
      s.qty[r.month_start] = Number(r.order_qty) || 0;
      s.total += Number(r.order_qty) || 0;
    }
    const all = [...byVendor.entries()].map(([vendor, bySku]) => {
      const skus = [...bySku.values()].filter(s => s.total > 0).sort((a, b) => b.total - a.total);
      const totals = {};
      for (const s of skus) for (const [k, n] of Object.entries(s.qty)) totals[k] = (totals[k] || 0) + n;
      return { vendor, skus, totals, total: skus.reduce((t, s) => t + s.total, 0) };
    });
    const rank = g => (g.vendor === "Unassigned" ? 1 : 0);
    const withOrder = all.filter(g => g.total > 0).sort((a, b) => rank(a) - rank(b) || b.total - a.total);
    return { withOrder, covered: all.filter(g => g.total === 0).map(g => g.vendor).sort() };
  });

  let timer = null;
  onMounted(() => { refresh(); timer = visibleInterval(refresh, POLL_INTERVAL_MS); });
  onUnmounted(() => clearInterval(timer));

  return { rows, loadError, loaded, computedAt, forecastAsof, months, groups, refresh };
}
