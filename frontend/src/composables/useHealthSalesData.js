// Health Card › 4.1 Net sales by SKU -- public.health_sales_monthly (one row per
// month x sku_id), from Jarvis via scripts/sync_health_sales.py (VPN laptop).
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

// Monthly figures that sync at most a few times a day -- 30 min is plenty.
const POLL_INTERVAL_MS = 30 * 60 * 1000;

export function useHealthSalesData() {
  const rows = ref([]);
  const loadError = ref("");
  const lastSynced = ref(null);

  async function refresh() {
    // ~9 SKUs x 6 months -- far under PostgREST's 1000-row page, no paging needed.
    const { data, error } = await supabase.from("health_sales_monthly").select("*");
    if (!error) {
      rows.value = data;
      lastSynced.value = data.reduce((m, r) => (r.synced_at > (m || "") ? r.synced_at : m), null);
    }
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

  return { rows, loadError, lastSynced, refresh };
}
