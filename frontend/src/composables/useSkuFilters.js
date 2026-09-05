// Column filters + top search for the SKU Level Data table -- same
// pattern as usePoFilters.js (reactive state, not manual DOM rebuilding,
// so typing never loses input focus across updates).
//
// resolveVendorLabel is optional -- pass it (admin.html) to enable the
// Vendor column/filter; omit it (vendor.html, already scoped to one
// vendor) and vendor filtering/fields are simply not part of the mix.
import { reactive, computed } from "vue";
import { fmtNum } from "../format.js";

export function useSkuFilters(sortedRows, resolveVendorLabel) {
  const filters = reactive({
    search: "", vendor: "", sku: "", item: "",
    qtyOrdered: "", qtyPending: "", qtyReceived: "", poCount: "", facilityCount: "",
  });

  function rowFields(agg) {
    return {
      vendor: resolveVendorLabel ? resolveVendorLabel(agg.vendor_code, agg.vendor_name) : "",
      sku: agg.item_sku || "",
      item: agg.item_name || "",
      qtyOrdered: fmtNum(agg.qty_ordered),
      qtyPending: fmtNum(agg.qty_pending),
      qtyReceived: fmtNum(agg.qty_received),
      poCount: String(agg.poCodes.size),
      facilityCount: String(agg.facilities.size),
    };
  }

  function matches(agg) {
    const fields = rowFields(agg);
    const f = filters;
    if (resolveVendorLabel && f.vendor && agg.vendor_code !== f.vendor) return false;
    for (const key of ["sku", "item", "qtyOrdered", "qtyPending", "qtyReceived", "poCount", "facilityCount"]) {
      if (f[key] && !fields[key].toLowerCase().includes(f[key].toLowerCase())) return false;
    }
    if (f.search) {
      const q = f.search.toLowerCase();
      if (!Object.values(fields).some(v => String(v).toLowerCase().includes(q))) return false;
    }
    return true;
  }

  const filteredSorted = computed(() => sortedRows.value.filter(matches));

  return { filters, filteredSorted };
}
