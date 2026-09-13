// Column filters + top search for the Shipment Tracking table -- same
// reactive-state pattern as useDispatchPlanningFilters.js.
import { reactive, computed } from "vue";
import { bluedartStatusLabel } from "../format.js";

export function useShipmentTrackingFilters(rows, resolveVendorLabel) {
  const filters = reactive({
    search: "", vendor: "", poCode: "", sku: "", awb: "", status: "",
  });

  function rowFields(row) {
    return {
      vendor: resolveVendorLabel ? resolveVendorLabel(row.vendor_code) : "",
      poCode: row.po_code || "",
      sku: row.item_sku || "",
      awb: row.awb_number || "",
      status: bluedartStatusLabel(row.tracking?.status_type),
    };
  }

  function matches(row) {
    const fields = rowFields(row);
    const f = filters;
    if (resolveVendorLabel && f.vendor && row.vendor_code !== f.vendor) return false;
    if (f.status && row.tracking?.status_type !== f.status) return false;
    for (const key of ["poCode", "sku", "awb"]) {
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
