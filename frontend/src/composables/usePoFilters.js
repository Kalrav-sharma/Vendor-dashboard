// Column filters + top search + sort for the PO Tracking table. Ported
// from poRowFields()/poMatchesFilters()/renderPoTracking() in the legacy
// docs/vendor.html and docs/admin.html.
//
// resolveVendorLabel is optional -- pass it (admin.html) to enable the
// Vendor column/filter; omit it (vendor.html, already scoped to one
// vendor) and vendor filtering/fields are simply not part of the mix.
//
// Being a real reactive composable (not manual innerHTML rebuilding) is
// what solves the "typing loses focus" problem the legacy pages had to
// work around with a manual shell/tbody split -- Vue's diffing keeps
// the actual <input> elements in place across reactive updates as long
// as the template structure doesn't change, so filters can just be plain
// reactive state.
import { reactive, computed } from "vue";
import { fmtNum, fmtMoney, STATUS_META, poSortComparator, dedupeInvoiceNumbers } from "../format.js";

export function usePoFilters(currentPos, grnsByPo, resolveVendorLabel) {
  const filters = reactive({
    search: "", vendor: "", facility: "", poCode: "",
    status: "", qtyOrdered: "", qtyReceived: "", poValue: "", grn: "",
  });

  function rowFields(p) {
    const grns = grnsByPo.value[p.po_code] || [];
    const invoices = dedupeInvoiceNumbers(grns.map(g => g.vendor_invoice_number));
    return {
      vendor: resolveVendorLabel ? resolveVendorLabel(p.vendor_code, p.vendor_name) : "",
      facility: p.facility || "",
      poCode: p.po_code || "",
      status: (STATUS_META[p.status] || [p.status])[0] || "",
      qtyOrdered: fmtNum(p.qty_ordered),
      qtyReceived: fmtNum(p.qty_received),
      poValue: fmtMoney(p.total_amount),
      grn: invoices.length ? invoices.join(", ") : "not yet raised",
    };
  }

  function matches(p) {
    const fields = rowFields(p);
    const f = filters;
    if (resolveVendorLabel && f.vendor && p.vendor_code !== f.vendor) return false;
    if (f.facility && fields.facility !== f.facility) return false;
    if (f.status && fields.status !== f.status) return false;
    for (const key of ["poCode", "qtyOrdered", "qtyReceived", "poValue", "grn"]) {
      if (f[key] && !fields[key].toLowerCase().includes(f[key].toLowerCase())) return false;
    }
    if (f.search) {
      const q = f.search.toLowerCase();
      if (!Object.values(fields).some(v => String(v).toLowerCase().includes(q))) return false;
    }
    return true;
  }

  const filteredSorted = computed(() => currentPos.value.filter(matches).sort(poSortComparator));

  const facilityOptions = computed(() =>
    [...new Set(currentPos.value.map(p => p.facility).filter(Boolean))].sort());
  const statusOptions = computed(() =>
    [...new Set(currentPos.value.map(p => (STATUS_META[p.status] || [p.status])[0]).filter(Boolean))].sort());

  return { filters, filteredSorted, facilityOptions, statusOptions };
}
