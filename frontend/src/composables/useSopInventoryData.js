// S&OP > Inventory Overview -- public.sop_inventory_channel (9 channel
// buckets x 6 SKUs, Croma+Vijay Sales merged into "MT" 2026-09-15) +
// public.sop_channel_drr_doi (DRR/DOI health view), synced from
// WH-Channel-SKU / Copy Daily Input Anish by scripts/sync_sop_inventory.py.
//
// Same shape as useRateCard.js: fetch on mount, poll every 60s so an
// already-open tab picks up the next sync without a manual refresh.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

// sync_sop_inventory.py runs every 30 min (4am-6pm) -- 5 min keeps this
// feeling live without polling unchanged data (2026-09-25, Kalrav).
const POLL_INTERVAL_MS = 5 * 60 * 1000;

export function useSopInventoryData() {
  const channelRows = ref([]);
  const channelDrrDoiRows = ref([]);
  const loadError = ref("");

  async function refresh() {
    const [{ data: c, error: e1 }, { data: d, error: e2 }] = await Promise.all([
      supabase.from("sop_inventory_channel").select("*"),
      supabase.from("sop_channel_drr_doi").select("*"),
    ]);
    if (!e1) channelRows.value = c;
    if (!e2) channelDrrDoiRows.value = d;
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

  return { channelRows, channelDrrDoiRows, loadError, refresh };
}
