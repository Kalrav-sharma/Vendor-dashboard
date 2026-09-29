<script setup>
import { ref, computed, onMounted } from "vue";
import { supabase, requireSession, ROLE_LABELS } from "./supabaseClient.js";
import { getViewOverride, getPreviewVendorCode, clearViewOverride } from "./viewOverride.js";
import { usePurchaseOrders } from "./composables/usePurchaseOrders.js";
import { usePoFilters } from "./composables/usePoFilters.js";
import { useSkuAggregates } from "./composables/useSkuAggregates.js";
import { useSkuFilters } from "./composables/useSkuFilters.js";
import { useDispatchPlanningFilters } from "./composables/useDispatchPlanningFilters.js";
import { useShipmentTracking } from "./composables/useShipmentTracking.js";
import { useModal } from "./composables/useModal.js";
import { useInvoiceUploads } from "./composables/useInvoiceUploads.js";
import { usePaymentFilters } from "./composables/usePaymentFilters.js";
import { dedupeInvoiceNumbers, dedupeVendorOptions, fmtDateOnly, fmtNum, TERMINAL_STATUSES, trackingBucket } from "./format.js";
import DashboardOverview from "./components/DashboardOverview.vue";
import SidebarNav from "./components/SidebarNav.vue";
import PoTrackingTable from "./components/PoTrackingTable.vue";
import SkuLevelTable from "./components/SkuLevelTable.vue";
import DispatchPlanningTable from "./components/DispatchPlanningTable.vue";
import PaymentDashboardTable from "./components/PaymentDashboardTable.vue";
import AppModal from "./components/AppModal.vue";
import PoDetailModal from "./components/PoDetailModal.vue";
import SkuDetailModal from "./components/SkuDetailModal.vue";
import SetNewPasswordForm from "./components/SetNewPasswordForm.vue";
import BrandLogo from "./components/BrandLogo.vue";
import ProfileMenu from "./components/ProfileMenu.vue";

const ready = ref(false);
const mustChangePassword = ref(false); // gates the whole dashboard until cleared
const myDisplayName = ref("Vendor"); // recorded on any invoice this login uploads
const myEmail = ref("");
const myRole = ref("vendor"); // real DB role -- stays "admin" even while previewing this view
const activeNav = ref("dashboard");
const pageTitle = computed(() => {
  if (activeNav.value === "dashboard") return `Welcome ${myDisplayName.value} Team`;
  return {
    "po-tracking": "Purchase Order",
    "sku-data": "SKU Level Data",
    "dispatch-planning": "Dispatch Planning",
    "payment-dashboard": "Payments",
  }[activeNav.value];
});
const todayLabel = computed(() => fmtDateOnly(new Date().toISOString().slice(0, 10)));

// Admin previewing a specific vendor (Profile > Switch view) -- a plain
// synchronous read, since it only ever matters for that one admin-only
// path; a real vendor login never has this set and RLS alone scopes them,
// exactly as before this existed.
const previewVendorCode = getPreviewVendorCode();
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
const { filters: paymentFilters, filteredSorted: paymentFilteredSorted, reconciliationOptions, paymentStatusOptions } = usePaymentFilters(allUploads);

// --- Dashboard tab ---
const DASH_ICONS = {
  document: '<svg viewBox="0 0 20 20"><path d="M6 2.5h6l3 3v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-14a1 1 0 0 1 1-1Z"/><path d="M12 2.5V6h3.5"/></svg>',
  clipboard: '<svg viewBox="0 0 20 20"><rect x="5" y="3.5" width="10" height="14" rx="1.5"/><rect x="7.5" y="2" width="5" height="3" rx="1"/><path d="M7.5 9h5M7.5 12h5M7.5 15h3"/></svg>',
  truck: '<svg viewBox="0 0 20 20"><path d="M2 6h9v8H2Z"/><path d="M11 9h3l3 3v2h-6V9Z"/><circle cx="6" cy="16" r="1.5"/><circle cx="14" cy="16" r="1.5"/></svg>',
  check: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5"/><path d="M6.5 10.2 8.8 12.5 13.5 7.5"/></svg>',
  percent: '<svg viewBox="0 0 20 20"><circle cx="6" cy="6" r="2"/><circle cx="14" cy="14" r="2"/><path d="M15 5 5 15"/></svg>',
};

// A PO "has an invoice shared" once any upload exists for it, regardless of
// reconciliation/payment outcome -- allUploads is the same flat per-login
// list the Payment Dashboard already reads, so this needs no extra fetch.
const poCodesWithInvoice = computed(() => new Set(allUploads.value.map((u) => u.po_code)));

const dashOpenPos = computed(() =>
  currentPos.value.filter((p) => !TERMINAL_STATUSES.has(p.status) && !poCodesWithInvoice.value.has(p.po_code)).length);
const dashDocketPending = computed(() => new Set(pendingDispatchRows.value.map((r) => r.po_code)).size);
const dashInTransit = computed(() =>
  new Set(shippedDispatchRows.value.filter((r) => trackingBucket(r) === "in_transit").map((r) => r.po_code)).size);
const NINETY_DAYS_MS = 90 * 24 * 60 * 60 * 1000;
const dashPoComplete = computed(() => {
  const cutoff = Date.now() - NINETY_DAYS_MS;
  return currentPos.value.filter((p) => p.status === "COMPLETE" && p.created_at && new Date(p.created_at).getTime() >= cutoff).length;
});
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
  { key: "open", label: "Open POs", value: dashOpenPos.value, sublabel: "Invoice not yet shared", icon: DASH_ICONS.document, colorVar: "--open" },
  { key: "docket", label: "Docket pending", value: dashDocketPending.value, sublabel: "Invoice shared · add docket", icon: DASH_ICONS.clipboard, colorVar: "--info" },
  { key: "transit", label: "In transit", value: dashInTransit.value, sublabel: "Tracking confirmed", icon: DASH_ICONS.truck, colorVar: "--accent" },
  { key: "complete", label: "PO complete", value: dashPoComplete.value, sublabel: "Closed · last 3 mo", icon: DASH_ICONS.check, colorVar: "--good" },
  {
    key: "fillrate", label: "Fill rate",
    value: dashFillRatePct.value == null ? "–" : `${dashFillRatePct.value.toFixed(1)}%`,
    sublabel: "Target 90%", icon: DASH_ICONS.percent,
    colorVar: dashFillRatePct.value == null ? "--muted" : dashFillRatePct.value >= 90 ? "--good" : "--critical",
  },
]);

const dashScorecardTiles = computed(() => {
  const fillOk = dashFillRatePct.value != null && dashFillRatePct.value >= 90;
  return [
    {
      key: "tat", label: "Avg TAT",
      value: dashAvgTatDays.value == null ? "–" : `${dashAvgTatDays.value.toFixed(0)}d`,
      sublabel: dashAvgTatDays.value == null ? "No dispatched shipments yet" : "", cls: "",
    },
    {
      key: "fillrate", label: "Fill rate",
      value: dashFillRatePct.value == null ? "–" : `${dashFillRatePct.value.toFixed(1)}%`,
      sublabel: dashFillRatePct.value == null ? "" : (fillOk ? "On target" : "Target: ≥90%"),
      cls: dashFillRatePct.value == null ? "" : (fillOk ? "good" : "critical"),
    },
  ];
});

const dashRecentPos = computed(() => currentPos.value.slice(0, 5).map((p) => ({
  po_code: p.po_code,
  sku_count: (poItemsByPo.value[p.po_code] || []).length,
  facility: p.facility || "",
})));

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
  myDisplayName.value = previewingAsAdmin
    ? (previewedVendorName || previewVendorCode || "Vendor")
    : (ctx.profile.vendor_name || ctx.profile.email || "Vendor");
  myEmail.value = ctx.profile.email || "";
  await fetchAllUploads(previewVendorCode);
  ready.value = true;
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

  <div v-else-if="ready" class="app-shell">
    <SidebarNav
      v-model="activeNav"
      brand="Vendor Portal"
      :items="[
        { id: 'dashboard', label: 'Dashboard' },
        { id: 'po-tracking', label: 'PO Tracking' },
        { id: 'sku-data', label: 'SKU Level Data' },
        { id: 'dispatch-planning', label: 'Dispatch Planning' },
        { id: 'payment-dashboard', label: 'Payments' },
      ]"
    >
      <template #account>
        <ProfileMenu :display-name="myDisplayName" :email="myEmail" :access="ROLE_LABELS[myRole] || ROLE_LABELS.vendor" :role="myRole" :vendors="previewVendorOptions" :on-sign-out="signOut" />
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
              <template v-else-if="activeNav === 'sku-data'">SKUs with at least one open purchase order not yet fully supplied, highest pending quantity first. Click a SKU for the PO-level breakdown.</template>
              <template v-else-if="activeNav === 'dispatch-planning'">Estimated dispatch date and quantity per SKU awaiting dispatch, plus live Bluedart status for every shipment you've already confirmed. Click a PO to see its details.</template>
              <template v-else-if="activeNav === 'payment-dashboard'">Every invoice you've uploaded, with its reconciliation and payment status. Click a PO to see its details.</template>
            </div>
          </div>
        </header>

        <div v-show="activeNav === 'dashboard'">
          <DashboardOverview
            :kpis="dashKpiTiles" :scorecard="dashScorecardTiles" :recent-pos="dashRecentPos"
            :on-open-po="openPoDetailModal"
          />
        </div>

        <div v-show="activeNav === 'po-tracking'">
          <PoTrackingTable
            :rows="filteredSorted" :filters="filters"
            :facility-options="facilityOptions" :status-options="statusOptions"
            :grns-by-po="grnsByPo" :show-buckets="true"
            :on-open-po="openPoDetailModal" :allow-invoice-upload="true" :uploader-label="myDisplayName"
            :sku-rows="skuFilteredSorted" :sku-filters="skuFilters" :on-open-sku="openSkuDetailModal"
          />
        </div>

        <div v-show="activeNav === 'sku-data'">
          <SkuLevelTable :rows="skuFilteredSorted" :filters="skuFilters" :on-open-sku="openSkuDetailModal" />
        </div>

        <div v-show="activeNav === 'dispatch-planning'">
          <DispatchPlanningTable :rows="dispatchFilteredSorted" :filters="dispatchFilters" :on-open-po="openPoDetailModal" />
        </div>

        <div v-show="activeNav === 'payment-dashboard'">
          <PaymentDashboardTable
            :rows="paymentFilteredSorted" :filters="paymentFilters" :reconciliation-options="reconciliationOptions"
            :payment-status-options="paymentStatusOptions"
            :on-open-po="openPoDetailModal" :uploader-label="myDisplayName"
            :show-vendor-kpis="true" :pos-needing-invoice-count="dashOpenPos"
          />
        </div>

        <footer class="page-foot">Data refreshes automatically every ~5 minutes from Uniware. {{ lastCheckedText }}</footer>
      </div>
    </div>

    <AppModal />
  </div>
</template>
