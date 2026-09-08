// Column filters + top search for the Dispatch Planning table -- same
// reactive-state pattern as usePoFilters.js/useSkuFilters.js.
//
// resolveVendorLabel is optional -- pass it (admin.html) to enable the
// Vendor column/filter; omit it (vendor.html, already scoped to one
// vendor) and vendor filtering/fields are simply not part of the mix.
import { reactive, computed } from "vue";
import { fmtNum, fmtDateOnly } from "../format.js";

export function useDispatchPlanningFilters(rows, resolveVendorLabel) {
  const filters = reactive({
    search: "", vendor: "", poCode: "", sku: "", item: "", qty: "", dispatchDate: "",
  });

  function rowFields(row) {
    return {
      vendor: resolveVendorLabel ? resolveVendorLabel(row.vendor_code) : "",
      poCode: row.po_code || "",
      sku: row.item_sku || "",
      item: row.item_name || "",
      qty: fmtNum(row.estimated_dispatch_qty),
      dispatchDate: fmtDateOnly(row.estimated_dispatch_date),
    };
  }

  function matches(row) {
    const fields = rowFields(row);
    const f = filters;
    if (resolveVendorLabel && f.vendor && row.vendor_code !== f.vendor) return false;
    for (const key of ["poCode", "sku", "item", "qty", "dispatchDate"]) {
      if (f[key] && !fields[key].toLowerCase().includes(f[key].toLowerCase())) return false;
    }
    if (f.search) {
      const q = f.search.toLowerCase();
      if (!Object.values(fields).some((v) => String(v).toLowerCase().includes(q))) return false;
    }
    return true;
  }

  const filteredSorted = computed(() => rows.value.filter(matches));

  return { filters, filteredSorted };
}
