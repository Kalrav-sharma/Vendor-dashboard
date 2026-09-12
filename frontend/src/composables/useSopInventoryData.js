// S&OP > Inventory Overview -- public.sop_inventory_channel (10 channel
// buckets x 6 SKUs) + public.sop_inventory_uc_warehouse (5 warehouses x 6
// SKUs, on-hand/in-transit/combined), synced from WH-Channel-SKU /
// Copy Daily Input Anish by scripts/sync_sop_inventory.py.
//
// Same shape as useRateCard.js: fetch on mount, poll every 60s so an
// already-open tab picks up the next sync without a manual refresh.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useSopInventoryData() {
  const channelRows = ref([]);
  const warehouseRows = ref([]);
  const loadError = ref("");

  async function refresh() {
    const [{ data: c, error: e1 }, { data: w, error: e2 }] = await Promise.all([
      supabase.from("sop_inventory_channel").select("*"),
      supabase.from("sop_inventory_uc_warehouse").select("*"),
    ]);
    if (!e1) channelRows.value = c;
    if (!e2) warehouseRows.value = w;
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

  return { channelRows, warehouseRows, loadError, refresh };
}
