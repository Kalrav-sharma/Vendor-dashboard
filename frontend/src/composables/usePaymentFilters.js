// Column filters + top search for the Payment Dashboard -- same
// reactive-state pattern as usePoFilters.js/useSkuFilters.js.
//
// resolveVendorLabel is optional -- pass it (admin.html) to enable the
// Vendor column/filter; omit it (vendor.html, already scoped to one
// vendor) and vendor filtering/fields are simply not part of the mix.
import { reactive, computed } from "vue";
import { fmtMoney, fmtDateOnly, paymentStatusLabel } from "../format.js";
import { reconciliationLabel } from "../reconciliation.js";

export function usePaymentFilters(rows, resolveVendorLabel) {
  const filters = reactive({
    search: "", vendor: "", poCode: "", invoiceNumber: "",
    invoiceValue: "", grnValue: "", dueDate: "", reconciliation: "", paymentStatus: "",
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
      paymentStatus: paymentStatusLabel(row.payment_status),
    };
  }

  function matches(row) {
    const fields = rowFields(row);
    const f = filters;
    if (resolveVendorLabel && f.vendor && row.vendor_code !== f.vendor) return false;
    if (f.reconciliation && fields.reconciliation !== f.reconciliation) return false;
    if (f.paymentStatus && fields.paymentStatus !== f.paymentStatus) return false;
    for (const key of ["poCode", "invoiceNumber", "invoiceValue", "grnValue", "dueDate"]) {
      if (f[key] && !fields[key].toLowerCase().includes(f[key].toLowerCase())) return false;
    }
    if (f.search) {
      const q = f.search.toLowerCase();
      if (!Object.values(fields).some((v) => String(v).toLowerCase().includes(q))) return false;
    }
    return true;
  }

  // Problem cases surface first (an in-progress check is grouped in here
  // too -- it's as unresolved as a real mismatch), then GRN Pending, with
  // fully reconciled invoices last -- Kalrav's explicit call, same order
  // for every role. Native Array#sort is a stable sort, so rows within
  // the same group keep their existing (created_at-desc) order.
  const STATUS_SORT_PRIORITY = { mismatch: 0, error: 0, pending: 0, needs_review: 1, matched: 2 };
  function statusSortPriority(row) {
    return STATUS_SORT_PRIORITY[row.match_status] ?? 0;
  }

  const filteredSorted = computed(() =>
    rows.value.filter(matches).sort((a, b) => statusSortPriority(a) - statusSortPriority(b)));

  const reconciliationOptions = computed(() =>
    [...new Set(rows.value.map((r) => reconciliationLabel(r).text))].sort());

  const paymentStatusOptions = computed(() =>
    [...new Set(rows.value.map((r) => paymentStatusLabel(r.payment_status)))].sort());

  return { filters, filteredSorted, reconciliationOptions, paymentStatusOptions };
}
