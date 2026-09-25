// Fetches po_item_shipments (the dispatch log, RLS-scoped to the caller --
// internal staff see all, a vendor sees only their own) and shipment_tracking
// (per-courier status per AWB, kept fresh by scripts/sync_bluedart_tracking.py
// and scripts/sync_dtdc_tracking.py), then merges them client-side by
// (courier, awb_number) -- not awb_number alone, since AWB numbers are only
// unique within one courier's own numbering -- same "join in JS, not SQL"
// convention as usePurchaseOrders.js. Polls every 60s, same pattern too.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

// The 3 courier tracking syncs (Bluedart/DTDC/Lets Transport) each run
// every 30 min -- 5 min still feels responsive without re-polling
// unchanged data 30x per real update (2026-09-25, Kalrav).
const POLL_INTERVAL_MS = 5 * 60 * 1000;

export function useShipmentTracking() {
  const rows = ref([]);
  const loadError = ref(null);

  async function refresh() {
    const [{ data: shipments, error: shipErr }, { data: tracking }] = await Promise.all([
      supabase.from("po_item_shipments").select("*").order("confirmed_at", { ascending: false }),
      supabase.from("shipment_tracking").select("*"),
    ]);

    if (shipErr) {
      loadError.value = shipErr.message;
      return;
    }
    loadError.value = null;

    const trackingByKey = {};
    for (const t of (tracking || [])) trackingByKey[`${t.courier}|${t.awb_number}`] = t;

    rows.value = (shipments || []).map((s) => ({ ...s, tracking: trackingByKey[`${s.courier}|${s.awb_number}`] || null }));
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
