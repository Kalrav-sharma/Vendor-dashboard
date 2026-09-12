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
    const { data, error } = await supabase.from("sop_sales_plan_actual").select("*");
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
