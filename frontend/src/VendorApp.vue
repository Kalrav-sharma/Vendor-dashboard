<script setup>
import { ref, computed, onMounted, watch } from "vue";
import { supabase, requireSession, ROLE_LABELS } from "./supabaseClient.js";
import { getViewOverride, getPreviewVendorCode, clearViewOverride } from "./viewOverride.js";
import { readNavHash, writeNavHash } from "./navHash.js";
import { usePurchaseOrders } from "./composables/usePurchaseOrders.js";
import { usePoFilters } from "./composables/usePoFilters.js";
import { useSkuAggregates } from "./composables/useSkuAggregates.js";
import { useSkuFilters } from "./composables/useSkuFilters.js";
import { useDispatchPlanningFilters } from "./composables/useDispatchPlanningFilters.js";
import { useShipmentTracking } from "./composables/useShipmentTracking.js";
import { useModal } from "./composables/useModal.js";
import { useInvoiceUploads } from "./composables/useInvoiceUploads.js";
import { useVendorRequests, isRequestAnswered } from "./composables/useVendorRequests.js";
import { usePaymentFilters } from "./composables/usePaymentFilters.js";
import { useSupportTickets } from "./composables/useSupportTickets.js";
import { dedupeInvoiceNumbers, dedupeVendorOptions, fmtDateOnly, fmtNum, trackingBucket, TERMINAL_STATUSES } from "./format.js";
import DashboardOverview from "./components/DashboardOverview.vue";
import MyPerformance from "./components/MyPerformance.vue";
import SidebarNav from "./components/SidebarNav.vue";
import PoTrackingTable from "./components/PoTrackingTable.vue";
import DispatchPlanningTable from "./components/DispatchPlanningTable.vue";
import PaymentDashboardTable from "./components/PaymentDashboardTable.vue";
import RaiseTicketTab from "./components/RaiseTicketTab.vue";
import AppModal from "./components/AppModal.vue";
import VendorRequestsPopup from "./components/VendorRequestsPopup.vue";
import PoDetailModal from "./components/PoDetailModal.vue";
import SkuDetailModal from "./components/SkuDetailModal.vue";
import SetNewPasswordForm from "./components/SetNewPasswordForm.vue";
import SetVendorEmailForm from "./components/SetVendorEmailForm.vue";
import BrandLogo from "./components/BrandLogo.vue";
import ProfileMenu from "./components/ProfileMenu.vue";

const ready = ref(false);
const mustChangePassword = ref(false); // gates the whole dashboard until cleared
const mustChangeEmail = ref(false); // one-time "confirm your real email" gate, checked after mustChangePassword
const myDisplayName = ref("Vendor"); // recorded on any invoice this login uploads
const myEmail = ref("");
const myRole = ref("vendor"); // real DB role -- stays "admin" even while previewing this view
// The tab in the URL hash (set by a previous visit or a shared link) wins
// over the "dashboard" default, so refreshing the page -- or reopening a
// bookmarked tab -- doesn't bounce back to the Dashboard. writeNavHash
// below keeps the hash in sync with every later tab change, from a click
// or from a Dashboard shortcut (navigateTo).
const NAV_IDS = ["dashboard", "po-tracking", "dispatch-planning", "payment-dashboard", "my-performance", "raise-ticket"];
const activeNav = ref(NAV_IDS.includes(readNavHash()) ? readNavHash() : "dashboard");
watch(activeNav, (id) => writeNavHash(id));
const pageTitle = computed(() => {
  if (activeNav.value === "dashboard") return `Welcome ${myDisplayName.value} Team`;
  return {
    "po-tracking": "Purchase Order",
    "dispatch-planning": "Dispatch Planning",
    "payment-dashboard": "Payments",
    "my-performance": "My Performance",
    "raise-ticket": "Raise a Ticket",
  }[activeNav.value];
});
const todayLabel = computed(() => fmtDateOnly(new Date().toISOString().slice(0, 10)));

// Admin previewing a specific vendor (Profile > Switch view) -- a plain
// synchronous read, since it only ever matters for that one admin-only
// path; a real vendor login never has this set and RLS alone scopes them,
// exactly as before this existed.
const previewVendorCode = getPreviewVendorCode();
const myVendorCode = ref(""); // this login's own vendor_code (or the previewed one, for an admin) -- tags a raised ticket
// Populated only when actually previewing as admin (see onMounted) -- this
// page's own currentPos below is deliberately scoped to just the vendor
// being previewed, so it can't supply a full vendor list the way
// AdminApp.vue's poVendorOptions does; this is a small separate unfiltered
// fetch instead, so Settings > Switch view can jump straight to a
// DIFFERENT vendor without detouring back through Management first.
const previewVendorOptions = ref([]);

const { currentPos, grnsByPo, poItemsByPo, grnItemsByPoSku, grnByCode, lastUpdated, invoicesForItem } = usePurchaseOrders(previewVendorCode);
const { filters, filteredSorted, facilityOptions, statusOptions } = usePoFilters(currentPos, grnsByPo);
const { sortedRows: skuRows } = useSkuAggregates(currentPos, poItemsByPo, { multiVendor: false });
const { filters: skuFilters, filteredSorted: skuFilteredSorted } = useSkuFilters(skuRows);

// Dispatch Planning shows the whole lifecycle of a SKU's dispatch, as one
// combined list (no separate "Shipment Tracking" section): rows still
// awaiting an estimate/dispatch (kind: "pending", one per po_items row with
// both estimate fields set) PLUS every already-confirmed shipment (kind:
// "shipped", one per po_item_shipments row, with its live Bluedart status
// attached) -- so entering an AWB and clicking "Dispatched" makes tracking
// info appear right here automatically, same table, no navigation required.
const pendingDispatchRows = computed(() => {
  const rows = [];
  for (const items of Object.values(poItemsByPo.value)) {
    for (const it of items) {
      if (it.estimated_dispatch_date != null && it.estimated_dispatch_qty != null) rows.push({ kind: "pending", ...it });
    }
  }
  return rows;
});

const { rows: shipmentRows } = useShipmentTracking(previewVendorCode);
const shippedDispatchRows = computed(() => shipmentRows.value.map((s) => {
  const item = (poItemsByPo.value[s.po_code] || []).find((it) => it.item_sku === s.item_sku);
  return { kind: "shipped", ...s, item_name: item?.item_name };
}));

const dispatchPlanningRows = computed(() => [...pendingDispatchRows.value, ...shippedDispatchRows.value]);
const { filters: dispatchFilters, filteredSorted: dispatchFilteredSorted } = useDispatchPlanningFilters(dispatchPlanningRows);

const { allUploads, fetchAllUploads } = useInvoiceUploads();

// Credit-note / corrected-invoice requests the team has raised on this vendor's invoices
// (admin PO Tracking › GRN pending). Still pending = not yet answered by an upload -- see
// isRequestAnswered(). Attached to the upload as vendor_request so the Payments table can
// bucket, highlight and offer the right upload on that row.
const { latestByUpload, fetchRequests } = useVendorRequests();
const pendingRequests = computed(() => {
  const out = [];
  for (const u of allUploads.value) {
    const req = u.payment_status === "paid" ? null : latestByUpload.value[u.id];
    if (!req) continue;
    if (!isRequestAnswered(req, allUploads.value.filter((x) => x.po_code === u.po_code))) out.push({ req, upload: u });
  }
  return out;
});
const uploadsWithRequests = computed(() => {
  const byUpload = Object.fromEntries(pendingRequests.value.map((r) => [r.upload.id, r.req]));
  return allUploads.value.map((u) => (byUpload[u.id] ? { ...u, vendor_request: byUpload[u.id] } : u));
});
const { filters: paymentFilters, filteredSorted: paymentFilteredSorted, reconciliationOptions, paymentStatusOptions } = usePaymentFilters(uploadsWithRequests);

// Shown once per browser session (sessionStorage) while any request is pending -- so every
// fresh login sees it again until they're answered, without it re-popping on each reload.
const requestsPopupOpen = ref(false);
const popupSeenKey = () => `vendor-requests-popup-seen:${myVendorCode.value}`;
function maybeShowRequestsPopup() {
  if (pendingRequests.value.length && !sessionStorage.getItem(popupSeenKey())) requestsPopupOpen.value = true;
}
function closeRequestsPopup() {
  requestsPopupOpen.value = false;
  sessionStorage.setItem(popupSeenKey(), "1");
}
function openRequestsBucket() {
  closeRequestsPopup();
  navigateTo("payment-dashboard", "requests");
}

const { tickets, fetchTickets, raiseTicket } = useSupportTickets();
const poCodeOptions = computed(() => currentPos.value.map((p) => p.po_code));

// --- Dashboard tab ---
const DASH_ICONS = {
  document: '<svg viewBox="0 0 20 20"><path d="M6 2.5h6l3 3v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-14a1 1 0 0 1 1-1Z"/><path d="M12 2.5V6h3.5"/></svg>',
  truck: '<svg viewBox="0 0 20 20"><path d="M2 6h9v8H2Z"/><path d="M11 9h3l3 3v2h-6V9Z"/><circle cx="6" cy="16" r="1.5"/><circle cx="14" cy="16" r="1.5"/></svg>',
  check: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5"/><path d="M6.5 10.2 8.8 12.5 13.5 7.5"/></svg>',
};

// A PO "has an invoice shared" once any upload exists for it, regardless of
// reconciliation/payment outcome -- allUploads is the same flat per-login
// list the Payment Dashboard already reads, so this needs no extra fetch.
const poCodesWithInvoice = computed(() => new Set(allUploads.value.map((u) => u.po_code)));

// Full PO rows (not just a count) behind the same "Invoice not yet shared"
// definition as dashOpenPos below -- the Payments tab's "Invoice Copy
// Needed" bucket lists and uploads directly against these, since a PO
// missing its invoice entirely has no po_invoice_uploads row to show in
// that table otherwise.
//
// A PO with zero uploads still isn't "needs invoice" if Finance already
// has it booked or paid on their own ledger (purchase_orders.payment_status,
// matched by PO code -- see sync_payment_status_manual.py's PO-level path)
// -- that PO needs nothing from the vendor either way, invoice or not, so
// it's split into posWithPaymentNoInvoice below instead.
//
// TERMINAL_STATUSES is deliberately NOT part of this check (removed
// 2026-09-30, measured against GELTRON: 6 of their 11 POs were COMPLETE
// with zero invoices AND no payout record either -- the old "only count
// still-open POs" rule hid exactly the POs MOST overdue for an invoice,
// since finishing delivery is what starts the payment clock, not what
// excuses skipping the invoice).
const posNeedingInvoice = computed(() =>
  currentPos.value.filter((p) => !poCodesWithInvoice.value.has(p.po_code) && !p.payment_status));
const posWithPaymentNoInvoice = computed(() =>
  currentPos.value.filter((p) => !poCodesWithInvoice.value.has(p.po_code) && !!p.payment_status));
const dashOpenPos = computed(() => posNeedingInvoice.value.length);
// "Supplied" = the vendor has confirmed at least one dispatch against the
// PO (shippedDispatchRows), regardless of tracking bucket -- replaces the
// old separate Docket pending / In transit tiles with one lifecycle stage:
// goods are out the door but the PO's own payment_status (Finance's
// ledger, not the per-invoice one) isn't "paid" yet.
const dashInProgress = computed(() => {
  const shippedPoCodes = new Set(shippedDispatchRows.value.map((r) => r.po_code));
  return currentPos.value.filter((p) => shippedPoCodes.has(p.po_code) && p.payment_status !== "paid").length;
});
// Same "complete" definition PO Tracking's own bucket uses (see poBucket()
// in PoTrackingTable.vue) -- terminal status AND every uploaded invoice
// paid, or Finance's own payment_status when nothing was uploaded through
// the portal at all. This tile used to count raw status === "COMPLETE"
// alone, which could show far more POs "complete" than PO Tracking's own
// PO-complete bucket did for the same vendor (2026-09-30, GELTRON: this
// tile said 6, PO Tracking's bucket said 1 -- COMPLETE status alone says
// nothing about whether payment actually settled).
function isPoComplete(p) {
  const uploads = allUploads.value.filter((u) => u.po_code === p.po_code);
  const fullyPaid = uploads.length ? uploads.every((u) => u.payment_status === "paid") : p.payment_status === "paid";
  return TERMINAL_STATUSES.has(p.status) && fullyPaid;
}
// Fixed window start (not a rolling N days), requested 2026-09-30 --
// August 1st of the current year.
const dashCompleteWindowStart = new Date(new Date().getFullYear(), 7, 1);
const dashPoComplete = computed(() =>
  currentPos.value.filter((p) => isPoComplete(p) && p.created_at && new Date(p.created_at) >= dashCompleteWindowStart).length);
const dashFillRatePct = computed(() => {
  const totalOrdered = currentPos.value.reduce((s, p) => s + (Number(p.qty_ordered) || 0), 0);
  const totalReceived = currentPos.value.reduce((s, p) => s + (Number(p.qty_received) || 0), 0);
  return totalOrdered > 0 ? (totalReceived / totalOrdered) * 100 : null;
});
// Days between a PO's creation and the confirmed dispatch date of each of
// its shipments -- no target/on-track threshold shown here, since nothing
// in this data tells us what this vendor's actual TAT commitment is.
const dashAvgTatDays = computed(() => {
  const posByCode = new Map(currentPos.value.map((p) => [p.po_code, p]));
  const withDates = shippedDispatchRows.value
    .map((r) => ({ r, po: posByCode.get(r.po_code) }))
    .filter(({ r, po }) => r.dispatched_date && po?.created_at);
  if (!withDates.length) return null;
  const totalDays = withDates.reduce((s, { r, po }) =>
    s + (new Date(r.dispatched_date) - new Date(po.created_at)) / (24 * 60 * 60 * 1000), 0);
  return totalDays / withDates.length;
});

const dashKpiTiles = computed(() => [
  { key: "open", label: "Open POs", value: dashOpenPos.value, sublabel: "Invoice not yet shared", icon: DASH_ICONS.document, colorVar: "--open", nav: "po-tracking", bucket: "invoice_needed" },
  { key: "progress", label: "In progress", value: dashInProgress.value, sublabel: "Supplied · payment pending", icon: DASH_ICONS.truck, colorVar: "--accent", nav: "po-tracking", bucket: "processing" },
  { key: "complete", label: "PO complete", value: dashPoComplete.value, sublabel: "Closed · since Aug 1", icon: DASH_ICONS.check, colorVar: "--good", nav: "po-tracking", bucket: "complete" },
]);

// Dashboard's "Delivery health" panel -- the headline slice of the My
// Performance scorecard, with the same targets.
const dashHealth = computed(() => {
  const fill = dashFillRatePct.value;
  const done = perfPoCompletionPct.value;
  return [
    {
      key: "fillrate", label: "Fill rate", value: fill == null ? "–" : `${fill.toFixed(1)}%`,
      cls: fill == null ? "" : fill >= 90 ? "good" : "critical",
      meter: fill == null ? null : { pct: fill, target: 90 }, sub: "Units received vs. ordered · target 90%",
    },
    {
      key: "completion", label: "PO completion", value: done == null ? "–" : `${done.toFixed(1)}%`,
      cls: done == null ? "" : done >= 90 ? "good" : "critical",
      meter: done == null ? null : { pct: done, target: 90 }, sub: "POs fully closed · target 90%",
    },
    {
      key: "tat", label: "Avg turnaround", value: dashAvgTatDays.value == null ? "–" : `${dashAvgTatDays.value.toFixed(0)} days`,
      sub: dashAvgTatDays.value == null ? "No dispatched shipments yet" : "PO created → dispatched",
    },
  ];
});

// "Needs your attention" -- every open item that's waiting on the vendor,
// each one a shortcut to the tab (and bucket) where it gets resolved.
// Same definitions the destination tabs already use, so the count here
// matches what the vendor lands on: posNeedingInvoice is the Payments
// tab's "Invoice Copy Needed" bucket; a mismatch without a credit note yet
// is its "CN Required" bucket (minus ones already answered); overdue plans
// and exceptions mirror DispatchPlanningTable's own KPI tiles.
const todayStart = new Date(new Date().toDateString());
const plural = (n, one, many) => (n === 1 ? one : many);
const dashActions = computed(() => {
  const invoices = posNeedingInvoice.value.length;
  const requested = pendingRequests.value.length;
  const requestedIds = new Set(pendingRequests.value.map((r) => r.upload.id));
  const creditNotes = allUploads.value.filter((u) => u.match_status === "mismatch" && !u.credit_note_storage_path && !requestedIds.has(u.id)).length;
  const overduePlans = pendingDispatchRows.value.filter((r) => new Date(r.estimated_dispatch_date) < todayStart).length;
  const exceptions = shippedDispatchRows.value.filter((r) => trackingBucket(r) === "exception").length;
  return [
    {
      key: "requests", count: requested, tone: "critical", nav: "payment-dashboard", bucket: "requests",
      title: plural(requested, "Invoice with a request from our team", "Invoices with a request from our team"),
      sub: "A credit note or a corrected invoice has been asked for", cta: "Respond",
    },
    {
      key: "invoices", count: invoices, tone: "critical", nav: "payment-dashboard", bucket: "invoice_copy_needed",
      title: plural(invoices, "PO waiting for an invoice copy", "POs waiting for an invoice copy"),
      sub: "Upload the invoice PDF so it can be reconciled and paid", cta: "Upload invoices",
    },
    {
      key: "credit", count: creditNotes, tone: "critical", nav: "payment-dashboard", bucket: "cn_required",
      title: plural(creditNotes, "Invoice needs a credit note", "Invoices need a credit note"),
      sub: "Reconciliation found a mismatch with the GRN", cta: "Review mismatches",
    },
    {
      key: "overdue", count: overduePlans, tone: "open", nav: "dispatch-planning",
      title: plural(overduePlans, "Dispatch plan past its date", "Dispatch plans past their date"),
      sub: "Dispatch the stock, or update the estimate on the PO", cta: "Open dispatch plan",
    },
    {
      key: "exceptions", count: exceptions, tone: "open", nav: "dispatch-planning",
      title: plural(exceptions, "Shipment with a delivery exception", "Shipments with a delivery exception"),
      sub: "Undelivered or returned — check with the courier", cta: "See shipments",
    },
  ];
});

// --- My Performance tab ---
// PO completion rate -- same isPoComplete() definition as Dashboard's "PO
// complete" tile, just as an all-time rate across every visible PO
// (see visiblePos() in format.js) instead of that tile's since-Aug-1 count.
const perfPoCompletionPct = computed(() => {
  if (!currentPos.value.length) return null;
  const complete = currentPos.value.filter((p) => isPoComplete(p)).length;
  return (complete / currentPos.value.length) * 100;
});

// Avg calendar days between uploading an invoice and it actually being
// paid -- only counts rows that have both a created_at (every row has
// one) and a payment_date (only rows the payout sync has actually
// settled), so an invoice still awaiting payment doesn't drag this down
// with a fake "0 days so far".
const perfAvgDaysToPaid = computed(() => {
  const paid = allUploads.value.filter((u) => u.payment_status === "paid" && u.payment_date && u.created_at);
  if (!paid.length) return null;
  const totalDays = paid.reduce((s, u) =>
    s + (new Date(u.payment_date) - new Date(u.created_at)) / (24 * 60 * 60 * 1000), 0);
  return totalDays / paid.length;
});

// Of every invoice this vendor has ever uploaded, what share needed a
// credit note (match_status "mismatch") vs. reconciled clean ("matched")
// -- the two rates that make up "payment health" alongside how fast
// payment actually lands above.
const perfCreditNoteRatePct = computed(() => {
  if (!allUploads.value.length) return null;
  return (allUploads.value.filter((u) => u.match_status === "mismatch").length / allUploads.value.length) * 100;
});
const perfMatchRatePct = computed(() => {
  if (!allUploads.value.length) return null;
  return (allUploads.value.filter((u) => u.match_status === "matched").length / allUploads.value.length) * 100;
});

const perfFulfillmentScorecard = computed(() => {
  const fillOk = dashFillRatePct.value != null && dashFillRatePct.value >= 90;
  const completeOk = perfPoCompletionPct.value != null && perfPoCompletionPct.value >= 90;
  return [
    { key: "tat", label: "Avg TAT", value: dashAvgTatDays.value == null ? "–" : `${dashAvgTatDays.value.toFixed(0)}d`,
      sublabel: dashAvgTatDays.value == null ? "No dispatched shipments yet" : "PO created → dispatched", cls: "" },
    { key: "fillrate", label: "Fill rate", value: dashFillRatePct.value == null ? "–" : `${dashFillRatePct.value.toFixed(1)}%`,
      sublabel: dashFillRatePct.value == null ? "" : (fillOk ? "On target" : "Target: ≥90%"), cls: dashFillRatePct.value == null ? "" : (fillOk ? "good" : "critical"),
      meter: dashFillRatePct.value == null ? null : { pct: dashFillRatePct.value, target: 90 } },
    { key: "completion", label: "PO completion rate", value: perfPoCompletionPct.value == null ? "–" : `${perfPoCompletionPct.value.toFixed(1)}%`,
      sublabel: perfPoCompletionPct.value == null ? "" : (completeOk ? "On target" : "Target: ≥90%"), cls: perfPoCompletionPct.value == null ? "" : (completeOk ? "good" : "critical"),
      meter: perfPoCompletionPct.value == null ? null : { pct: perfPoCompletionPct.value, target: 90 } },
  ];
});
const perfPaymentHealthScorecard = computed(() => {
  const cnOk = perfCreditNoteRatePct.value != null && perfCreditNoteRatePct.value <= 10;
  const matchOk = perfMatchRatePct.value != null && perfMatchRatePct.value >= 90;
  return [
    { key: "daystopaid", label: "Avg days to get paid", value: perfAvgDaysToPaid.value == null ? "–" : `${perfAvgDaysToPaid.value.toFixed(0)}d`,
      sublabel: perfAvgDaysToPaid.value == null ? "No paid invoices yet" : "Upload → payment", cls: "" },
    { key: "cnrate", label: "Credit note rate", value: perfCreditNoteRatePct.value == null ? "–" : `${perfCreditNoteRatePct.value.toFixed(1)}%`,
      sublabel: perfCreditNoteRatePct.value == null ? "" : (cnOk ? "On target" : "Target: ≤10%"), cls: perfCreditNoteRatePct.value == null ? "" : (cnOk ? "good" : "critical"),
      meter: perfCreditNoteRatePct.value == null ? null : { pct: perfCreditNoteRatePct.value, target: 10, lowerIsBetter: true } },
    { key: "matchrate", label: "Reconciliation match rate", value: perfMatchRatePct.value == null ? "–" : `${perfMatchRatePct.value.toFixed(1)}%`,
      sublabel: perfMatchRatePct.value == null ? "" : (matchOk ? "On target" : "Target: ≥90%"), cls: perfMatchRatePct.value == null ? "" : (matchOk ? "good" : "critical"),
      meter: perfMatchRatePct.value == null ? null : { pct: perfMatchRatePct.value, target: 90 } },
  ];
});

const dashRecentPos = computed(() => currentPos.value.slice(0, 5).map((p) => ({
  po_code: p.po_code,
  sku_count: (poItemsByPo.value[p.po_code] || []).length,
  facility: p.facility || "",
  status: p.status,
  total_amount: p.total_amount,
})));

// Dashboard shortcuts: switch tab, and (for the two bucketed tables) land
// on the matching bucket -- see focusBucket in PoTrackingTable.vue.
const poFocus = ref(null);
const paymentFocus = ref(null);
function navigateTo(navId, bucket) {
  if (bucket && navId === "po-tracking") poFocus.value = { bucket };
  if (bucket && navId === "payment-dashboard") paymentFocus.value = { bucket };
  activeNav.value = navId;
  window.scrollTo({ top: 0 });
}

const NAV_ICONS = {
  dashboard: '<svg viewBox="0 0 20 20"><rect x="3" y="3" width="6" height="6" rx="1.5"/><rect x="11" y="3" width="6" height="6" rx="1.5"/><rect x="3" y="11" width="6" height="6" rx="1.5"/><rect x="11" y="11" width="6" height="6" rx="1.5"/></svg>',
  payments: '<svg viewBox="0 0 20 20"><rect x="2.5" y="5" width="15" height="10.5" rx="1.5"/><path d="M2.5 8.5h15"/><path d="M6 12.5h3"/></svg>',
  performance: '<svg viewBox="0 0 20 20"><path d="M3 16.5h14"/><path d="M5.5 13.5v-3"/><path d="M10 13.5v-7"/><path d="M14.5 13.5v-5"/></svg>',
  ticket: '<svg viewBox="0 0 20 20"><path d="M3.5 5h13a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1H8l-3.5 3v-3h-1a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z"/></svg>',
};
const navItems = computed(() => [
  { id: "dashboard", label: "Dashboard", icon: NAV_ICONS.dashboard },
  { id: "po-tracking", label: "Purchase Orders", icon: DASH_ICONS.document },
  { id: "dispatch-planning", label: "Dispatch Planning", icon: DASH_ICONS.truck },
  { id: "payment-dashboard", label: "Payments", icon: NAV_ICONS.payments, badge: pendingRequests.value.length || null, pulse: true },
  { id: "my-performance", label: "My Performance", icon: NAV_ICONS.performance },
  { id: "raise-ticket", label: "Raise a Ticket", icon: NAV_ICONS.ticket },
]);

const scopeLine = computed(() => `${currentPos.value.length} purchase order${currentPos.value.length === 1 ? "" : "s"} on file`);
const lastCheckedText = computed(() => lastUpdated.value
  ? "Page last checked " + lastUpdated.value.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
  : "");

const { open: openModal } = useModal();

function openPoDetailModal(poCode) {
  const po = currentPos.value.find(p => p.po_code === poCode);
  if (!po) return;
  const items = (poItemsByPo.value[poCode] || []).map(item => ({ ...item, invoiceText: invoicesForItem(item) }));
  const grns = grnsByPo.value[poCode] || [];
  const invoices = dedupeInvoiceNumbers(grns.map(g => g.vendor_invoice_number));
  openModal("Purchase Order", PoDetailModal, {
    po, items, invoices, allowInvoiceUpload: true, allowDispatchPlanning: true, uploaderLabel: myDisplayName.value,
  }, poCode);
}

function openSkuDetailModal(key) {
  const found = skuRows.value.find(a => a.key === key);
  if (!found) return;
  openModal(found.item_name || found.item_sku, SkuDetailModal, { agg: found, onOpenPo: openPoDetailModal }, `(${found.item_sku})`);
}

onMounted(async () => {
  const ctx = await requireSession();
  if (!ctx) return;
  // An admin previewing this view (Profile > Switch view) is the one
  // exception to "vendor role only" -- everyone else still gets bounced.
  const previewingAsAdmin = ctx.profile.role === "admin" && getViewOverride() === "vendor";
  if (ctx.profile.role !== "vendor" && !previewingAsAdmin) {
    window.location.href = "admin.html";
    return;
  }
  myRole.value = ctx.profile.role;
  let previewedVendorName = null;
  if (previewingAsAdmin) {
    // Unfiltered on purpose -- this admin's RLS access already spans every
    // vendor; it's just listing them, not reading anyone's PO details.
    // Merges both directions of the same gap AdminApp.vue's own
    // poVendorOptions does: a vendor_code with real POs but no login yet,
    // or a login that exists before any PO has landed (a freshly-created
    // placeholder) -- either way it's previewable, just an empty dashboard
    // in the zero-PO case.
    const [{ data: pos }, { data: logins }] = await Promise.all([
      supabase.from("purchase_orders").select("vendor_code, vendor_name"),
      supabase.from("profiles").select("vendor_code, vendor_name").eq("role", "vendor"),
    ]);
    const allVendors = [...(logins || []), ...(pos || [])];
    previewVendorOptions.value = dedupeVendorOptions(allVendors);
    // The vendor actually being previewed, not the admin's own vendor_name
    // (staff profiles reuse that column for their own display name, e.g.
    // "Praneeth Kumar" -- showing that here instead of the previewed
    // vendor's name was a real bug).
    previewedVendorName = allVendors.find(v => v.vendor_code === previewVendorCode)?.vendor_name;
  }
  if (ctx.profile.must_change_password) {
    mustChangePassword.value = true;
    return;
  }
  // Vendor-only (never the previewing-admin branch, whose own profile is
  // an admin's, not a vendor's) -- forces a real email in place of whatever
  // placeholder the login was created with, once, before the dashboard.
  if (ctx.profile.role === "vendor" && ctx.profile.must_change_email) {
    mustChangeEmail.value = true;
    return;
  }
  myDisplayName.value = previewingAsAdmin
    ? (previewedVendorName || previewVendorCode || "Vendor")
    : (ctx.profile.vendor_name || ctx.profile.email || "Vendor");
  myEmail.value = ctx.profile.email || "";
  myVendorCode.value = previewingAsAdmin ? (previewVendorCode || "") : (ctx.profile.vendor_code || "");
  await Promise.all([fetchAllUploads(previewVendorCode), fetchTickets(previewVendorCode), fetchRequests(previewVendorCode)]);
  ready.value = true;
  maybeShowRequestsPopup();
});

async function handlePasswordChanged() {
  // Re-fetch so we pick up the freshly-cleared must_change_password and the
  // profile fields the dashboard needs, rather than trusting stale state.
  const ctx = await requireSession();
  if (!ctx) return;
  mustChangePassword.value = false;
  myRole.value = ctx.profile.role;
  myDisplayName.value = ctx.profile.vendor_name || ctx.profile.email || "Vendor";
  myEmail.value = ctx.profile.email || "";
  ready.value = true;
}

async function handleEmailChanged() {
  // Doubles as the "Skip for now" handler below -- either way just moves
  // past the gate for this session. If an email was actually saved,
  // must_change_email is already cleared server-side and this re-fetch
  // picks that up; if skipped, it's still true in the DB, so simply not
  // re-checking it here (rather than persisting a dismissal) is what makes
  // the gate correctly reappear next time this vendor logs in.
  const ctx = await requireSession();
  if (!ctx) return;
  mustChangeEmail.value = false;
  myRole.value = ctx.profile.role;
  myDisplayName.value = ctx.profile.vendor_name || ctx.profile.email || "Vendor";
  myEmail.value = ctx.profile.email || "";
  myVendorCode.value = ctx.profile.vendor_code || "";
  await Promise.all([fetchAllUploads(previewVendorCode), fetchTickets(previewVendorCode), fetchRequests(previewVendorCode)]);
  ready.value = true;
  maybeShowRequestsPopup();
}

async function signOut() {
  // So a leftover preview from this session can never affect whoever
  // signs into this browser next.
  clearViewOverride();
  await supabase.auth.signOut();
  window.location.href = "login.html";
}
</script>

<template>
  <div v-if="mustChangePassword" class="auth-shell">
    <div class="auth-card">
      <BrandLogo brand="native" class="login-logo" />
      <h1>Set a new password</h1>
      <div class="sub">For security, please set your own password before continuing -- this account was created with a shared temporary password.</div>
      <SetNewPasswordForm submit-label="Set password and continue" @done="handlePasswordChanged" />
    </div>
  </div>

  <div v-else-if="mustChangeEmail" class="auth-shell">
    <div class="auth-card">
      <BrandLogo brand="native" class="login-logo" />
      <h1>Confirm your email</h1>
      <div class="sub">Please add your own email address before continuing -- this account was created with a placeholder email.</div>
      <SetVendorEmailForm submit-label="Save email and continue" @done="handleEmailChanged" />
      <button type="button" class="link-btn" style="margin-top: 12px;" @click="handleEmailChanged">Skip for now</button>
    </div>
  </div>

  <div v-else-if="ready" class="app-shell vendor-shell">
    <SidebarNav
      v-model="activeNav"
      brand="Vendor Portal"
      :items="navItems"
    >
      <template #account>
        <ProfileMenu :display-name="myDisplayName" :email="myEmail" :access="ROLE_LABELS[myRole] || ROLE_LABELS.vendor" :role="myRole" :vendor-code="myVendorCode" :vendors="previewVendorOptions" :on-sign-out="signOut" />
      </template>
    </SidebarNav>

    <div class="main-content">
      <div class="wrap">
        <header class="page-head">
          <div>
            <h1>{{ pageTitle }}</h1>
            <div class="scope">
              <template v-if="activeNav === 'dashboard'">Performance snapshot - {{ todayLabel }}</template>
              <template v-else-if="activeNav === 'po-tracking'">{{ scopeLine }}</template>
              <template v-else-if="activeNav === 'dispatch-planning'">Estimated dispatch date and quantity per SKU awaiting dispatch, plus live Bluedart status for every shipment you've already confirmed. Click a PO to see its details.</template>
              <template v-else-if="activeNav === 'payment-dashboard'">Every invoice you've uploaded, with its reconciliation and payment status. Click a PO to see its details.</template>
              <template v-else-if="activeNav === 'my-performance'">Your fulfillment and payment-health scorecard, all-time.</template>
              <template v-else-if="activeNav === 'raise-ticket'">Raise an issue with our team and track its status here.</template>
            </div>
          </div>
        </header>

        <div v-show="activeNav === 'dashboard'" class="tab-panel">
          <DashboardOverview
            :actions="dashActions" :kpis="dashKpiTiles" :health="dashHealth"
            :recent-pos="dashRecentPos" :on-open-po="openPoDetailModal" :on-navigate="navigateTo"
          />
        </div>

        <div v-show="activeNav === 'po-tracking'" class="tab-panel">
          <PoTrackingTable
            :rows="filteredSorted" :filters="filters"
            :facility-options="facilityOptions" :status-options="statusOptions"
            :grns-by-po="grnsByPo" :show-buckets="true"
            :on-open-po="openPoDetailModal" :allow-invoice-upload="true" :uploader-label="myDisplayName"
            :sku-rows="skuFilteredSorted" :sku-filters="skuFilters" :on-open-sku="openSkuDetailModal"
            :focus-bucket="poFocus"
          />
        </div>

        <div v-show="activeNav === 'dispatch-planning'" class="tab-panel">
          <DispatchPlanningTable :rows="dispatchFilteredSorted" :filters="dispatchFilters" :on-open-po="openPoDetailModal" />
        </div>

        <div v-show="activeNav === 'payment-dashboard'" class="tab-panel">
          <PaymentDashboardTable
            :rows="paymentFilteredSorted" :filters="paymentFilters" :reconciliation-options="reconciliationOptions"
            :payment-status-options="paymentStatusOptions"
            :on-open-po="openPoDetailModal" :uploader-label="myDisplayName"
            :show-vendor-kpis="true" :show-buckets="true" :pos-needing-invoice="posNeedingInvoice"
            :pos-with-payment="posWithPaymentNoInvoice" :focus-bucket="paymentFocus"
          />
        </div>

        <div v-show="activeNav === 'my-performance'" class="tab-panel">
          <MyPerformance :fulfillment="perfFulfillmentScorecard" :payment-health="perfPaymentHealthScorecard" />
        </div>

        <div v-show="activeNav === 'raise-ticket'" class="tab-panel">
          <RaiseTicketTab
            :tickets="tickets" :po-code-options="poCodeOptions"
            :vendor-code="myVendorCode" :vendor-name="myDisplayName" :submitter-name="myDisplayName"
            :on-raise-ticket="raiseTicket"
          />
        </div>

        <footer class="page-foot">Data refreshes automatically every ~5 minutes from Uniware. {{ lastCheckedText }}</footer>
      </div>
    </div>

    <AppModal />
    <VendorRequestsPopup
      v-if="requestsPopupOpen" :items="pendingRequests" :uploader-label="myDisplayName"
      @close="closeRequestsPopup" @view-all="openRequestsBucket"
    />
  </div>
</template>
