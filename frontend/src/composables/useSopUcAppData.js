// S&OP > UC App + PLS -- public.sop_inventory_uc_warehouse (5 warehouses x 6
// SKUs, on-hand/in-transit/combined) + public.sop_dark_store_inventory (21
// individual dark stores, grouped by city) + public.sop_facility_drr_doi
// (WAREHOUSE + DARK_STORE rows), synced by scripts/sync_sop_inventory.py.
//
// Also reads public.sop_uniware_inventory purely for its synced_at, so the
// "On hand Inventory" view can say how fresh the Uniware stock actually is --
// on-hand comes straight from that snapshot now, and a stalled Uniware sync
// would otherwise keep serving yesterday's numbers with nothing to show it.
//
// Same shape as useSopInventoryData.js: fetch on mount, poll every 60s.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useSopUcAppData() {
  const warehouseRows = ref([]);
  const darkStoreRows = ref([]);
  const facilityDrrDoiRows = ref([]);
  const stockSyncedAt = ref(null);
  const loadError = ref("");

  async function refresh() {
    const [{ data: w, error: e1 }, { data: d, error: e2 }, { data: f, error: e3 }, { data: s }] =
      await Promise.all([
        supabase.from("sop_inventory_uc_warehouse").select("*"),
        supabase.from("sop_dark_store_inventory").select("*"),
        supabase.from("sop_facility_drr_doi").select("*"),
        // Every row of a run carries the same synced_at, so the newest one dates the whole snapshot.
        supabase.from("sop_uniware_inventory").select("synced_at")
          .order("synced_at", { ascending: false }).limit(1),
      ]);
    if (!e1) warehouseRows.value = w;
    if (!e2) darkStoreRows.value = d;
    if (!e3) facilityDrrDoiRows.value = f;
    // Deliberately not folded into loadError: a missing freshness stamp shouldn't
    // blank out tables that loaded fine.
    stockSyncedAt.value = s?.[0]?.synced_at ?? null;
    loadError.value = e1?.message || e2?.message || e3?.message || "";
  }

  let intervalId = null;
  onMounted(async () => {
    await refresh();
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => {
    if (intervalId) clearInterval(intervalId);
  });

  return { warehouseRows, darkStoreRows, facilityDrrDoiRows, stockSyncedAt, loadError, refresh };
}
