// Column filters + top search for the PO Fulfillment table. date/warehouse/
// channel/sku/status are checkbox multi-selects: null = All, else a Set.
import { reactive, computed } from "vue";

export function useSopPoFulfillmentFilters(rows) {
  const filters = reactive({ search: "", date: null, warehouse: null, channel: null, sku: null, status: null });

  function matches(r) {
    const f = filters;
    if (f.date && !f.date.has(r.sim_date)) return false;
    if (f.warehouse && !f.warehouse.has(r.warehouse)) return false;
    if (f.channel && !f.channel.has(r.channel)) return false;
    if (f.sku && !f.sku.has(r.sku)) return false;
    if (f.status && !f.status.has(r.status)) return false;
    if (f.search) {
      const q = f.search.toLowerCase();
      const haystack = [r.po_number, r.warehouse, r.channel, r.sku, r.status, r.detail].join(" ").toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  }

  const filteredSorted = computed(() =>
    rows.value.filter(matches).sort((a, b) => a.sim_date.localeCompare(b.sim_date) || a.warehouse.localeCompare(b.warehouse)));

  const dateOptions = computed(() => [...new Set(rows.value.map(r => r.sim_date))].sort());
  const warehouseOptions = computed(() => [...new Set(rows.value.map(r => r.warehouse))].sort());
  const channelOptions = computed(() => [...new Set(rows.value.map(r => r.channel))].sort());
  const skuOptions = computed(() => [...new Set(rows.value.map(r => r.sku))].sort());
  const statusOptions = computed(() => [...new Set(rows.value.map(r => r.status))].sort());

  return { filters, filteredSorted, dateOptions, warehouseOptions, channelOptions, skuOptions, statusOptions };
}
