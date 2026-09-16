// S&OP > Production Plan -- public.sop_production_daily (live Planned/
// Actual/Delta + facility split, sourced from "Day wise trackr"'s "Combined
// Planned Production" / "Combined Actual Production" headers). Note:
// production_plan_snapshots also exists (a frozen daily capture, still
// populated by sync_sop_production_snapshot.py) but is no longer read
// here -- Planned Production always shows this table's live planned_qty
// per Anish's explicit instruction; sync_sop_po_fulfillment.py is the only
// remaining consumer of the snapshot table.
import { ref, onMounted, onUnmounted } from "vue";
import { fetchAllRows } from "./sopPagedFetch.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useSopProductionData() {
  const dailyRows = ref([]);
  const loadError = ref("");

  async function refresh() {
    const { data, error } = await fetchAllRows("sop_production_daily");
    if (!error) dailyRows.value = data;
    loadError.value = error?.message || "";
  }

  let intervalId = null;
  onMounted(async () => {
    await refresh();
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => {
    if (intervalId) clearInterval(intervalId);
  });

  return { dailyRows, loadError, refresh };
}
