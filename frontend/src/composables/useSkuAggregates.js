// Aggregates open (non-terminal), not-yet-fully-supplied POs by SKU (or
// by vendor+SKU when multiVendor is true, so two vendors sharing an
// item_sku never get their pending quantities merged). Ported from
// computeSkuAggregates() in the legacy docs/vendor.html and docs/admin.html.
import { computed } from "vue";
import { TERMINAL_STATUSES } from "../format.js";

export function useSkuAggregates(currentPos, poItemsByPo, { multiVendor = false } = {}) {
  const aggregates = computed(() => {
    const openPos = currentPos.value.filter(p => !TERMINAL_STATUSES.has(p.status));
    const map = {};
    for (const p of openPos) {
      for (const item of (poItemsByPo.value[p.po_code] || [])) {
        const pending = Number(item.pending_quantity) || 0;
        if (pending <= 0) continue; // this SKU line on this PO is already fully supplied
        const key = multiVendor ? `${p.vendor_code}|${item.item_sku}` : item.item_sku;
        const agg = (map[key] ||= {
          key,
          vendor_code: p.vendor_code,
          vendor_name: p.vendor_name,
          item_sku: item.item_sku,
          item_name: item.item_name,
          qty_ordered: 0, qty_pending: 0, qty_received: 0,
          poCodes: new Set(), facilities: new Set(), details: [],
        });
        agg.qty_ordered += Number(item.quantity) || 0;
        agg.qty_pending += pending;
        agg.qty_received += Number(item.received_quantity) || 0;
        agg.poCodes.add(p.po_code);
        agg.facilities.add(p.facility);
        agg.details.push({
          po_code: p.po_code, facility: p.facility, status: p.status,
          qty_ordered: item.quantity, qty_pending: item.pending_quantity,
          qty_received: item.received_quantity, unit_price: item.unit_price,
        });
      }
    }
    return map;
  });

  const sortedRows = computed(() =>
    Object.values(aggregates.value).sort((a, b) => b.qty_pending - a.qty_pending));

  return { aggregates, sortedRows };
}
