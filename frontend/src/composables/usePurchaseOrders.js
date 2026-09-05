// Fetches purchase_orders/grns/po_items/grn_items from Supabase, builds
// the same derived lookup maps the legacy pages hand-built, and polls
// every 60s -- ported from loadAndRender()/loadData() in the legacy
// vendor.html/admin.html.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";
import { visiblePos } from "../format.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function usePurchaseOrders() {
  const currentPos = ref([]);
  const grnsByPo = ref({});
  const grnByCode = ref({});
  const poItemsByPo = ref({});
  const grnItemsByPoSku = ref({});
  const lastUpdated = ref(null);
  const loadError = ref(null);

  async function refresh() {
    const [{ data: pos, error: poErr }, { data: grns }, { data: poItems }, { data: grnItems }] = await Promise.all([
      supabase.from("purchase_orders").select("*").order("created_at", { ascending: false }),
      supabase.from("grns").select("*"),
      supabase.from("po_items").select("*"),
      supabase.from("grn_items").select("*"),
    ]);

    if (poErr) {
      loadError.value = poErr.message;
      return;
    }
    loadError.value = null;

    currentPos.value = visiblePos(pos);
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
    const invoices = [...new Set(
      items.map(gi => grnByCode.value[gi.grn_code]?.vendor_invoice_number).filter(Boolean)
    )];
    return invoices.join(", ") || "–";
  }

  let intervalId = null;
  onMounted(async () => {
    await refresh();
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => {
    if (intervalId) clearInterval(intervalId);
  });

  return { currentPos, grnsByPo, grnByCode, poItemsByPo, grnItemsByPoSku, lastUpdated, loadError, refresh, invoicesForItem };
}
