// Shared formatting/status helpers -- ported from the legacy
// docs/assets/app-common.js. escapeHtml() from that file has no
// equivalent here: Vue's templates auto-escape interpolated text, so
// there's nothing to port for that specific concern.

export const TERMINAL_STATUSES = new Set(["COMPLETE", "REJECTED", "CANCELLED", "CLOSED"]);

// Rejected and not-yet-approved POs are hidden from both the vendor and
// admin views entirely (Kalrav's explicit requirement) -- CREATED is
// Uniware's "drafted, not yet approved" status.
export const HIDDEN_STATUSES = new Set(["REJECTED", "CREATED"]);

export const STATUS_META = {
  COMPLETE: ["Complete", "good"],
  APPROVED: ["Approved · open", "open"],
  CREATED: ["Created · open", "open"],
  REJECTED: ["Rejected", "critical"],
  CANCELLED: ["Cancelled", "muted"],
};

export function fmtNum(n) {
  return (n === null || n === undefined) ? "–" : Number(n).toLocaleString("en-IN");
}

export function fmtMoney(n) {
  return (n === null || n === undefined) ? "–" : "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function fmtDate(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }) + ", " +
         d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

export function statusLabel(status) {
  return (STATUS_META[status] || [status || "Unknown"])[0];
}

export function statusClass(status) {
  return (STATUS_META[status] || [null, "muted"])[1];
}

export function visiblePos(pos) {
  return pos.filter(p => !HIDDEN_STATUSES.has(p.status));
}

// Open/approved POs before completed ones; within each of those two
// groups, largest PO value first.
export function poSortComparator(a, b) {
  const aGroup = TERMINAL_STATUSES.has(a.status) ? 1 : 0;
  const bGroup = TERMINAL_STATUSES.has(b.status) ? 1 : 0;
  if (aGroup !== bGroup) return aGroup - bGroup;
  return (Number(b.total_amount) || 0) - (Number(a.total_amount) || 0);
}
