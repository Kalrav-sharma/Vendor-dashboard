// S&OP > Day-on-Day Sales -- public.sop_daily_sales (one row per date x
// series x dim), synced from WH-Channel-SKU's "actual sales" tab by
// scripts/sync_sop_sales.py.
import { ref, onMounted, onUnmounted } from "vue";
import { fetchAllRows } from "./sopPagedFetch.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useSopDailySalesData() {
  const rows = ref([]);
  const loadError = ref("");

  async function refresh() {
    const { data, error } = await fetchAllRows("sop_daily_sales");
    if (!error) rows.value = data;
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

  return { rows, loadError, refresh };
}
