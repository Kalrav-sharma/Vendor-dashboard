// Last Mile Tracking -- reads the latest sync run's rows across all six
// last_mile_* tables, synced by scripts/sync_last_mile.py.
//
// Same shape as useSopInventoryData.js: fetch on mount, poll every 60s so
// an already-open tab picks up the next sync without a manual refresh.
//
// All six tables are keyed by run_id -- this composable first finds the
// latest run (order by generated_at desc limit 1), then fetches every
// other table scoped to that one run_id. Until a sync has run at least
// once, `run` is null and every list stays empty -- the page renders an
// empty state rather than an error, same convention as every other
// not-yet-synced tab in this app.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

// sync-last-mile.yml's tracking leg runs hourly (10:30am-11:30pm) -- 10
// min keeps this well ahead of the source without polling unchanged
// data 60x per real update (2026-09-25, Kalrav).
const POLL_INTERVAL_MS = 10 * 60 * 1000;

// last_mile_open_shipments can hold 1,000+ rows per run (the whole live+
// backlog population, not the curated alerts subset) -- past Supabase's
// default per-request row cap, so a plain .select() would silently
// truncate. Pages through with .range() until a page comes back short.
async function fetchAllPaged(table, runId, orderCol, pageSize = 1000) {
  const rows = [];
  for (let from = 0; ; from += pageSize) {
    const { data, error } = await supabase.from(table).select("*")
      .eq("run_id", runId).order(orderCol, { ascending: false })
      .range(from, from + pageSize - 1);
    if (error) return { data: null, error };
    rows.push(...(data || []));
    if (!data || data.length < pageSize) break;
  }
  return { data: rows, error: null };
}

export function useLastMileData() {
  const run = ref(null);
  const coverage = ref(null);
  const dqSummary = ref(null);
  const lspPerf = ref([]);
  const worstLanes = ref([]);
  const alerts = ref([]);
  const openShipments = ref([]);
  const loadError = ref("");

  async function refresh() {
    const { data: runs, error: runErr } = await supabase
      .from("last_mile_run").select("*").order("generated_at", { ascending: false }).limit(1);
    if (runErr) {
      loadError.value = runErr.message;
      return;
    }
    const latest = runs?.[0] || null;
    run.value = latest;

    if (!latest) {
      coverage.value = null;
      dqSummary.value = null;
      lspPerf.value = [];
      worstLanes.value = [];
      alerts.value = [];
      openShipments.value = [];
      loadError.value = "";
      return;
    }

    const [
      { data: cov, error: e1 },
      { data: dq, error: e2 },
      { data: lsp, error: e3 },
      { data: lanes, error: e4 },
      { data: al, error: e5 },
      { data: openRows, error: e6 },
    ] = await Promise.all([
      supabase.from("last_mile_coverage").select("*").eq("run_id", latest.run_id).maybeSingle(),
      supabase.from("last_mile_dq_summary").select("*").eq("run_id", latest.run_id).maybeSingle(),
      supabase.from("last_mile_lsp_perf").select("*").eq("run_id", latest.run_id).order("on_time_pct"),
      supabase.from("last_mile_worst_lanes").select("*").eq("run_id", latest.run_id).order("on_time_pct"),
      supabase.from("last_mile_alerts").select("*").eq("run_id", latest.run_id).order("days_overdue", { ascending: false }),
      fetchAllPaged("last_mile_open_shipments", latest.run_id, "days_overdue"),
    ]);

    coverage.value = cov || null;
    dqSummary.value = dq || null;
    lspPerf.value = lsp || [];
    worstLanes.value = lanes || [];
    alerts.value = al || [];
    openShipments.value = openRows || [];
    loadError.value = e1?.message || e2?.message || e3?.message || e4?.message || e5?.message || e6?.message || "";
  }

  let intervalId = null;
  onMounted(async () => {
    await refresh();
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => {
    if (intervalId) clearInterval(intervalId);
  });

  return { run, coverage, dqSummary, lspPerf, worstLanes, alerts, openShipments, loadError, refresh };
}
