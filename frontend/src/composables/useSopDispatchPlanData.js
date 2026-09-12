// S&OP > Channel Dispatch Plan -- same "find the latest run_date, filter
// every query by it" shape as useSopPoFulfillmentData.js, since these
// tables are also wholesale-replaced per run.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";
import { fetchAllRows } from "./sopPagedFetch.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useSopDispatchPlanData() {
  const planRows = ref([]);
  const productionCheckRows = ref([]);
  const runDate = ref(null);
  const loadError = ref("");

  async function refresh() {
    const { data: latest, error: e0 } = await supabase
      .from("sop_dispatch_plan").select("run_date").order("run_date", { ascending: false }).limit(1);
    if (e0) { loadError.value = e0.message; return; }
    const latestRunDate = latest?.[0]?.run_date || null;
    runDate.value = latestRunDate;
    if (!latestRunDate) { planRows.value = []; productionCheckRows.value = []; return; }

    const [{ data: p, error: e1 }, { data: c, error: e2 }] = await Promise.all([
      fetchAllRows("sop_dispatch_plan", q => q.eq("run_date", latestRunDate)),
      fetchAllRows("sop_dispatch_production_check", q => q.eq("run_date", latestRunDate)),
    ]);
    if (!e1) planRows.value = p;
    if (!e2) productionCheckRows.value = c;
    loadError.value = e1?.message || e2?.message || "";
  }

  let intervalId = null;
  onMounted(async () => {
    await refresh();
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => {
    if (intervalId) clearInterval(intervalId);
  });

  return { planRows, productionCheckRows, runDate, loadError, refresh };
}
