// Column filters + top search for the PO Fulfillment table -- same shape
// as usePoFilters.js.
import { reactive, computed } from "vue";

export function useSopPoFulfillmentFilters(rows) {
  const filters = reactive({ search: "", warehouse: "", channel: "", sku: "", status: "" });

  function matches(r) {
    const f = filters;
    if (f.warehouse && r.warehouse !== f.warehouse) return false;
    if (f.channel && r.channel !== f.channel) return false;
    if (f.sku && r.sku !== f.sku) return false;
    if (f.status && r.status !== f.status) return false;
    if (f.search) {
      const q = f.search.toLowerCase();
      const haystack = [r.po_number, r.warehouse, r.channel, r.sku, r.status, r.detail].join(" ").toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  }

  const filteredSorted = computed(() =>
    rows.value.filter(matches).sort((a, b) => a.sim_date.localeCompare(b.sim_date) || a.warehouse.localeCompare(b.warehouse)));

  const warehouseOptions = computed(() => [...new Set(rows.value.map(r => r.warehouse))].sort());
  const channelOptions = computed(() => [...new Set(rows.value.map(r => r.channel))].sort());
  const skuOptions = computed(() => [...new Set(rows.value.map(r => r.sku))].sort());
  const statusOptions = computed(() => [...new Set(rows.value.map(r => r.status))].sort());

  return { filters, filteredSorted, warehouseOptions, channelOptions, skuOptions, statusOptions };
}
