// Fetches purchase_orders/grns/po_items/grn_items from Supabase, builds
// the same derived lookup maps the legacy pages hand-built, and polls
// every 60s -- ported from loadAndRender()/loadData() in the legacy
// vendor.html/admin.html.
import { ref, onMounted, onUnmounted } from "vue";
import { fetchAllRows } from "./sopPagedFetch.js";
import { visiblePos, dedupeInvoiceNumbers } from "../format.js";

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
    // Paged: every one of these tables can outgrow PostgREST's 1,000-row-per-request cap,
    // which truncates silently -- po_items passed it first (1,048 rows on 2026-10-06), leaving
    // ~24 POs with no line items in the admin PO detail popup, and grn_items is close behind.
    const byVendor = vendorCode ? (q) => q.eq("vendor_code", vendorCode) : null;
    const [{ data: posUnsorted, error: poErr }, { data: grns }, { data: poItems }, { data: grnItems }] = await Promise.all([
      fetchAllRows("purchase_orders", byVendor, "po_code"),
      fetchAllRows("grns", byVendor, "grn_code"),
      fetchAllRows("po_items", byVendor),
      fetchAllRows("grn_items", byVendor),
    ]);
    // Newest first, as the single unpaged query used to return them (Postgres DESC puts nulls first).
    const pos = posUnsorted && [...posUnsorted].sort((a, b) =>
      (b.created_at == null) - (a.created_at == null) || String(b.created_at).localeCompare(String(a.created_at)));

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
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => {
    if (intervalId) clearInterval(intervalId);
  });

  return { currentPos, grnsByPo, grnByCode, poItemsByPo, grnItemsByPoSku, lastUpdated, loadError, refresh, invoicesForItem };
}
