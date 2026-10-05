// Fetches purchase_orders/grns/po_items/grn_items from Supabase, builds
// the same derived lookup maps the legacy pages hand-built, and polls
// every 60s -- ported from loadAndRender()/loadData() in the legacy
// vendor.html/admin.html.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";
import { visiblePos, dedupeInvoiceNumbers } from "../format.js";
import { setVisibleInterval, clearVisibleInterval } from "./polling.js";

// Matches refresh.yml's own 5-min Uniware sync cadence -- polling faster
// than the source can change just burned Supabase egress/log quota for
// no fresher data (2026-09-25, Kalrav).
const POLL_INTERVAL_MS = 5 * 60 * 1000;

// `vendorCode`: admin-only "preview as vendor" support (see viewOverride.js)
// -- an admin's RLS access spans every vendor, so without this filter the
// Vendor shell would show every vendor's rows mixed together instead of
// one vendor's actual view. A real vendor login never passes this; RLS
// alone already scopes them to their own vendor_code, exactly as before.
export function usePurchaseOrders(vendorCode = null) {
  const currentPos = ref([]);
  const grnsByPo = ref({});
  const grnByCode = ref({});
  const poItemsByPo = ref({});
  const grnItemsByPoSku = ref({});
  const lastUpdated = ref(null);
  const loadError = ref(null);

  async function refresh() {
    let posQuery = supabase.from("purchase_orders").select("*").order("created_at", { ascending: false });
    let grnsQuery = supabase.from("grns").select("*");
    let poItemsQuery = supabase.from("po_items").select("*");
    let grnItemsQuery = supabase.from("grn_items").select("*");
    if (vendorCode) {
      posQuery = posQuery.eq("vendor_code", vendorCode);
      grnsQuery = grnsQuery.eq("vendor_code", vendorCode);
      poItemsQuery = poItemsQuery.eq("vendor_code", vendorCode);
      grnItemsQuery = grnItemsQuery.eq("vendor_code", vendorCode);
    }
    const [{ data: pos, error: poErr }, { data: grns }, { data: poItems }, { data: grnItems }] = await Promise.all([
      posQuery, grnsQuery, poItemsQuery, grnItemsQuery,
    ]);

    if (poErr) {
      loadError.value = poErr.message;
      return;
    }
    loadError.value = null;

    const poCodesWithGrn = new Set((grns || []).map(g => g.po_code));
    currentPos.value = visiblePos(pos, poCodesWithGrn);
    const visibleCodes = new Set(currentPos.value.map(p => p.po_code));

    const newGrnsByPo = {};
    const newGrnByCode = {};
    for (const g of (grns || [])) {
      (newGrnsByPo[g.po_code] ||= []).push(g);
      newGrnByCode[g.grn_code] = g;
    }
    grnsByPo.value = newGrnsByPo;
    grnByCode.value = newGrnByCode;

    const newPoItemsByPo = {};
    for (const it of (poItems || [])) {
      if (visibleCodes.has(it.po_code)) (newPoItemsByPo[it.po_code] ||= []).push(it);
    }
    poItemsByPo.value = newPoItemsByPo;

    const newGrnItemsByPoSku = {};
    for (const gi of (grnItems || [])) {
      if (!visibleCodes.has(gi.po_code)) continue;
      const key = gi.po_code + "|" + gi.item_sku;
      (newGrnItemsByPoSku[key] ||= []).push(gi);
    }
    grnItemsByPoSku.value = newGrnItemsByPoSku;

    lastUpdated.value = new Date();
  }

  function invoicesForItem(item) {
    const key = item.po_code + "|" + item.item_sku;
    const items = grnItemsByPoSku.value[key] || [];
    const invoices = dedupeInvoiceNumbers(
      items.map(gi => grnByCode.value[gi.grn_code]?.vendor_invoice_number)
    );
    return invoices.join(", ") || "–";
  }

  let intervalId = null;
  onMounted(async () => {
    await refresh();
    intervalId = setVisibleInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => {
    if (intervalId) clearVisibleInterval(intervalId);
  });

  return { currentPos, grnsByPo, grnByCode, poItemsByPo, grnItemsByPoSku, lastUpdated, loadError, refresh, invoicesForItem };
}
