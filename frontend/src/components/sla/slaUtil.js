// Small shared helpers for the SLA › RCA panes.

// City-type filter semantics, same as the /late-delivery-rca dashboard's dropdowns:
// "Top 9" = Metro + Top9Other, and "Other" excludes both.
export const CITY_FILTERS = [
  { id: "all", label: "All cities" },
  { id: "Metro", label: "Metro" },
  { id: "Top9", label: "Top 9" },
  { id: "Other", label: "Other" },
];
export function matchGroup(cityGroup, filter) {
  if (filter === "all") return true;
  if (filter === "Metro") return cityGroup === "Metro";
  if (filter === "Top9") return cityGroup === "Metro" || cityGroup === "Top9Other";
  return cityGroup === "OtherCity";
}

export const SEV = {
  dispatch: "dispatch", transit: "transit", cx_dependency: "cx", both: "both", no_data: "nodata", unclear: "nodata",
};
export const BUCKET_SEV = {
  "a.1d breached": "dispatch", "b.2d breached": "dispatch", "c.3d breached": "both", "d.>3d breached": "both",
};
export const CLASS_LABEL = { lsp_constraint: "LSP Constraint", cx_dependency: "CX Dependency", unclassified: "Unclassified" };
export const CLASS_SEV = { lsp_constraint: "transit", cx_dependency: "cx", unclassified: "nodata" };

export const pct = (v, d = 1) => (v == null || Number.isNaN(v) ? "–" : `${Number(v).toFixed(d)}%`);
export const days = v => (v == null ? "–" : `${Number(v).toFixed(1)}d`);
export const dash = v => (v == null || v === "" ? "–" : v);

// On-time % bands, the portal's existing convention (LastMileCarrierTab.vue).
export const otdCls = p => (p == null ? "" : p < 70 ? "cell-critical" : p >= 90 ? "cell-good" : "cell-open");

export function downloadCsv(filename, headers, rows) {
  const esc = v => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [headers.map(esc).join(","), ...rows.map(r => r.map(esc).join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}
