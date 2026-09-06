// Column filters + top search for the Payment Dashboard -- same
// reactive-state pattern as usePoFilters.js/useSkuFilters.js.
//
// resolveVendorLabel is optional -- pass it (admin.html) to enable the
// Vendor column/filter; omit it (vendor.html, already scoped to one
// vendor) and vendor filtering/fields are simply not part of the mix.
import { reactive, computed } from "vue";
import { fmtMoney, fmtDateOnly } from "../format.js";
import { reconciliationLabel } from "../reconciliation.js";

export function usePaymentFilters(rows, resolveVendorLabel) {
  const filters = reactive({
    search: "", vendor: "", poCode: "", invoiceNumber: "",
    invoiceValue: "", grnValue: "", dueDate: "", reconciliation: "",
  });

  function rowFields(row) {
    return {
      vendor: resolveVendorLabel ? resolveVendorLabel(row.vendor_code) : "",
      poCode: row.po_code || "",
      invoiceNumber: row.match_details?.extracted?.invoice_number || "",
      invoiceValue: fmtMoney(row.match_details?.invoice_value ?? null),
      grnValue: fmtMoney(row.match_details?.grn_value ?? null),
      dueDate: fmtDateOnly(row.match_details?.invoice_due_date || null),
      reconciliation: reconciliationLabel(row).text,
    };
  }

  function matches(row) {
    const fields = rowFields(row);
    const f = filters;
    if (resolveVendorLabel && f.vendor && row.vendor_code !== f.vendor) return false;
    if (f.reconciliation && fields.reconciliation !== f.reconciliation) return false;
    for (const key of ["poCode", "invoiceNumber", "invoiceValue", "grnValue", "dueDate"]) {
      if (f[key] && !fields[key].toLowerCase().includes(f[key].toLowerCase())) return false;
    }
    if (f.search) {
      const q = f.search.toLowerCase();
      if (!Object.values(fields).some((v) => String(v).toLowerCase().includes(q))) return false;
    }
    return true;
  }

  const filteredSorted = computed(() => rows.value.filter(matches));

  const reconciliationOptions = computed(() =>
    [...new Set(rows.value.map((r) => reconciliationLabel(r).text))].sort());

  return { filters, filteredSorted, reconciliationOptions };
}
