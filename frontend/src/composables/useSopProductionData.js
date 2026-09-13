// S&OP > Production Plan -- public.sop_production_daily (live Planned/
// Actual/Delta + facility split) + public.production_plan_snapshots
// (frozen daily-captured planned figures, used for any past date instead
// of this table's live planned_qty, which may have already flipped to
// the actual by the time anyone looks -- see sync_sop_production.py).
import { ref, onMounted, onUnmounted } from "vue";
import { fetchAllRows } from "./sopPagedFetch.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useSopProductionData() {
  const dailyRows = ref([]);
  const snapshotRows = ref([]);
  const loadError = ref("");

  async function refresh() {
    const [{ data: d, error: e1 }, { data: s, error: e2 }] = await Promise.all([
      fetchAllRows("sop_production_daily"),
      fetchAllRows("production_plan_snapshots"),
    ]);
    if (!e1) dailyRows.value = d;
    if (!e2) snapshotRows.value = s;
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

  return { dailyRows, snapshotRows, loadError, refresh };
}
