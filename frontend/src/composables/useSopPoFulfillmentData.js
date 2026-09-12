// S&OP > PO Fulfillment -- these 3 tables are wholesale-replaced once per
// run_date (variable row count per run, not a stable upsert key), so this
// composable first finds the latest available run_date, then filters
// every query by it -- a plain unscoped select would mix rows from
// whichever runs happen to still be in the table (there should only ever
// be one, but this is the correct query regardless).
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";
import { fetchAllRows } from "./sopPagedFetch.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useSopPoFulfillmentData() {
  const rows = ref([]);
  const actionItems = ref([]);
  const rca = ref([]);
  const runDate = ref(null);
  const loadError = ref("");

  async function refresh() {
    const { data: latest, error: e0 } = await supabase
      .from("sop_po_fulfillment_daily").select("run_date").order("run_date", { ascending: false }).limit(1);
    if (e0) { loadError.value = e0.message; return; }
    const latestRunDate = latest?.[0]?.run_date || null;
    runDate.value = latestRunDate;
    if (!latestRunDate) { rows.value = []; actionItems.value = []; rca.value = []; return; }

    const [{ data: r, error: e1 }, { data: a, error: e2 }, { data: c, error: e3 }] = await Promise.all([
      fetchAllRows("sop_po_fulfillment_daily", q => q.eq("run_date", latestRunDate)),
      fetchAllRows("sop_po_action_items", q => q.eq("run_date", latestRunDate)),
      fetchAllRows("sop_po_shortfall_rca", q => q.eq("run_date", latestRunDate)),
    ]);
    if (!e1) rows.value = r;
    if (!e2) actionItems.value = a;
    if (!e3) rca.value = c;
    loadError.value = e1?.message || e2?.message || e3?.message || "";
  }

  let intervalId = null;
  onMounted(async () => {
    await refresh();
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => {
    if (intervalId) clearInterval(intervalId);
  });

  return { rows, actionItems, rca, runDate, loadError, refresh };
}
