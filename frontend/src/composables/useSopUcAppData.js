// S&OP > UC App + PLS -- public.sop_inventory_uc_warehouse (5 warehouses x 6
// SKUs, on-hand/in-transit/combined) + public.sop_facility_drr_doi
// (WAREHOUSE rows this phase; DARK_STORE rows land in the Uniware-sync
// follow-up), synced by scripts/sync_sop_inventory.py.
//
// Same shape as useSopInventoryData.js: fetch on mount, poll every 60s.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useSopUcAppData() {
  const warehouseRows = ref([]);
  const facilityDrrDoiRows = ref([]);
  const loadError = ref("");

  async function refresh() {
    const [{ data: w, error: e1 }, { data: f, error: e2 }] = await Promise.all([
      supabase.from("sop_inventory_uc_warehouse").select("*"),
      supabase.from("sop_facility_drr_doi").select("*"),
    ]);
    if (!e1) warehouseRows.value = w;
    if (!e2) facilityDrrDoiRows.value = f;
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

  return { warehouseRows, facilityDrrDoiRows, loadError, refresh };
}
