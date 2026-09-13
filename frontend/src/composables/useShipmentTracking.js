// Fetches po_item_shipments (the dispatch log, RLS-scoped to the caller --
// internal staff see all, a vendor sees only their own) and shipment_tracking
// (Bluedart status per AWB, kept fresh by scripts/sync_bluedart_tracking.py),
// then merges them client-side by awb_number -- same "join in JS, not SQL"
// convention as usePurchaseOrders.js. Polls every 60s, same pattern too.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

const POLL_INTERVAL_MS = 60 * 1000;

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

    const trackingByAwb = {};
    for (const t of (tracking || [])) trackingByAwb[t.awb_number] = t;

    rows.value = (shipments || []).map((s) => ({ ...s, tracking: trackingByAwb[s.awb_number] || null }));
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
