// S&OP > Daily Dispatch Planner -- output of the first-mile dispatch engine
// (scripts/vendor/first_mile_dispatch.js, the same engine the
// /first-mile-dispatch-decision skill runs), synced by scripts/sync_sop_first_mile.py.
//
// Five tables, all keyed (run_date, scenario): the truck plan, the plant-side FG/Hold/
// production picture, the PO fill rate, per-facility utilization, and the individual
// POs the plan leaves short. Both scenarios
// (with and without today's production) are computed server-side and stored together,
// so the tab's toggle is a pure filter -- no recomputation, and the two are always from
// the same run and therefore genuinely comparable.
//
// Same shape as the other S&OP composables: fetch on mount, poll every 60s.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

// sync_sop_first_mile.py runs 3x/day (0 3,9,15) -- 20 min is still far
// ahead of the source without polling unchanged data (2026-09-25, Kalrav).
const POLL_INTERVAL_MS = 20 * 60 * 1000;

export function useSopFirstMileData() {
  const planRows = ref([]);
  const plantRows = ref([]);
  const fillRows = ref([]);
  const utilRows = ref([]);
  const missedRows = ref([]);
  const runDate = ref("");
  const loadError = ref("");

  async function refresh() {
    // Scoped to the newest run_date so a part-written or superseded run can't blend into
    // the current one -- the engine emits a whole plan or none, and half of two different
    // plans is worse than either.
    const { data: latest, error: e0 } = await supabase
      .from("sop_first_mile_plan")
      .select("run_date")
      .order("run_date", { ascending: false })
      .limit(1);
    if (e0) { loadError.value = e0.message; return; }
    const rd = latest?.[0]?.run_date;
    if (!rd) { runDate.value = ""; return; }
    runDate.value = rd;

    const [{ data: p, error: e1 }, { data: pl, error: e2 }, { data: f, error: e3 },
           { data: u, error: e4 }, { data: mi, error: e5 }] = await Promise.all([
      supabase.from("sop_first_mile_plan").select("*").eq("run_date", rd),
      supabase.from("sop_first_mile_plant").select("*").eq("run_date", rd),
      supabase.from("sop_first_mile_fill_rate").select("*").eq("run_date", rd),
      supabase.from("sop_first_mile_facility_util").select("*").eq("run_date", rd),
      supabase.from("sop_first_mile_missed_po").select("*").eq("run_date", rd)
        .order("po_date").order("warehouse"),
    ]);
    if (!e1) planRows.value = p;
    if (!e2) plantRows.value = pl;
    if (!e3) fillRows.value = f;
    if (!e4) utilRows.value = u;
    if (!e5) missedRows.value = mi;
    loadError.value = e1?.message || e2?.message || e3?.message || e4?.message || e5?.message || "";
  }

  let intervalId = null;
  onMounted(async () => {
    await refresh();
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => {
    if (intervalId) clearInterval(intervalId);
  });

  return { planRows, plantRows, fillRows, utilRows, missedRows, runDate, loadError, refresh };
}
