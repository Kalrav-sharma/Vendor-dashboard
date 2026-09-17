// Column filters + top search for the Dispatch Planning table -- same
// reactive-state pattern as usePoFilters.js/useSkuFilters.js.
//
// Rows are a mix of two kinds (see AdminApp.vue/VendorApp.vue's
// dispatchPlanningRows): "pending" (still awaiting dispatch, estimate
// fields only) and "shipped" (already dispatched, AWB + live Bluedart
// tracking attached) -- rowFields() reads whichever fields apply to each
// kind so filtering/search works uniformly across both.
//
// resolveVendorLabel is optional -- pass it (admin.html) to enable the
// Vendor column/filter; omit it (vendor.html, already scoped to one
// vendor) and vendor filtering/fields are simply not part of the mix.
import { reactive, computed } from "vue";
import { fmtNum, fmtDateOnly, bluedartStatusLabel } from "../format.js";

export function useDispatchPlanningFilters(rows, resolveVendorLabel) {
  const filters = reactive({
    search: "", vendor: "", poCode: "", sku: "", item: "", qty: "", dispatchDate: "", awb: "", courier: "", status: "",
  });

  function rowFields(row) {
    return {
      vendor: resolveVendorLabel ? resolveVendorLabel(row.vendor_code) : "",
      poCode: row.po_code || "",
      sku: row.item_sku || "",
      item: row.item_name || "",
      qty: fmtNum(row.kind === "shipped" ? row.dispatched_qty : row.estimated_dispatch_qty),
      dispatchDate: fmtDateOnly(row.kind === "shipped" ? row.dispatched_date : row.estimated_dispatch_date),
      awb: row.kind === "shipped" ? (row.awb_number || "") : "",
      // Bluedart gets its friendly label; DTDC's status_text is already
      // human-readable ("Delivered") so it's used as-is. Either way this
      // only feeds the free-text search box -- the Status column FILTER
      // dropdown (DispatchPlanningTable.vue) still only lists Bluedart's
      // short codes, so picking a value there won't match a DTDC row yet
      // (worth widening once DTDC's status vocabulary is fully known).
      status: row.kind !== "shipped" ? "" : row.courier === "bluedart"
        ? bluedartStatusLabel(row.tracking?.status_type)
        : (row.tracking?.status_text || ""),
    };
  }

  function matches(row) {
    const fields = rowFields(row);
    const f = filters;
    if (resolveVendorLabel && f.vendor && row.vendor_code !== f.vendor) return false;
    if (f.courier && (row.kind !== "shipped" || row.courier !== f.courier)) return false;
    if (f.status && (row.kind !== "shipped" || row.tracking?.status_type !== f.status)) return false;
    for (const key of ["poCode", "sku", "item", "qty", "dispatchDate", "awb"]) {
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
