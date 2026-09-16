// S&OP > Sales: Plan vs Actual -- public.sop_sales_plan_actual (current
// month, channel x SKU), synced from WH-Channel-SKU's Dashboard + actual
// sales tabs by scripts/sync_sop_sales.py.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useSopSalesData() {
  const rows = ref([]);
  const loadError = ref("");

  async function refresh() {
    // sop_sales_plan_actual is upserted (never wholesale-replaced), so every prior month's rows
    // stay in the table forever -- must filter to the current month explicitly, otherwise a stale
    // month's row can silently win when the frontend has no ordering guarantee to rely on.
    const currentMonthStart = new Date().toISOString().slice(0, 7) + "-01";
    const { data, error } = await supabase.from("sop_sales_plan_actual").select("*")
      .eq("month_start", currentMonthStart);
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
