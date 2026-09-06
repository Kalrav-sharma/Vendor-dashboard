<script setup>
import { ref, computed, onMounted } from "vue";
import { supabase, requireSession, INTERNAL_ROLES } from "./supabaseClient.js";
import { usePurchaseOrders } from "./composables/usePurchaseOrders.js";
import { usePoFilters } from "./composables/usePoFilters.js";
import { useSkuAggregates } from "./composables/useSkuAggregates.js";
import { useSkuFilters } from "./composables/useSkuFilters.js";
import { useVendors } from "./composables/useVendors.js";
import { useTeam } from "./composables/useTeam.js";
import { useModal } from "./composables/useModal.js";
import { useInvoiceUploads } from "./composables/useInvoiceUploads.js";
import { usePaymentFilters } from "./composables/usePaymentFilters.js";
import { dedupeInvoiceNumbers } from "./format.js";
import SidebarNav from "./components/SidebarNav.vue";
import PoTrackingTable from "./components/PoTrackingTable.vue";
import SkuLevelTable from "./components/SkuLevelTable.vue";
import PaymentDashboardTable from "./components/PaymentDashboardTable.vue";
import ManageVendors from "./components/ManageVendors.vue";
import ManageTeam from "./components/ManageTeam.vue";
import AppModal from "./components/AppModal.vue";
import PoDetailModal from "./components/PoDetailModal.vue";
import SkuDetailModal from "./components/SkuDetailModal.vue";
import ProfileMenu from "./components/ProfileMenu.vue";

const ready = ref(false);
const whoLine = ref("Admin");
const myEmail = ref("");
const myRole = ref("admin");

// Page/action access per role -- see schema.sql's header comment for the
// full role model. RLS itself doesn't distinguish between these four
// (is_internal_staff() grants all of them the same underlying data
// visibility); this is purely a frontend concern.
const canSeePoTracking = computed(() => ["admin", "management", "operations"].includes(myRole.value));
const canSeeSkuData = computed(() => ["admin", "management", "operations"].includes(myRole.value));
const canSeePaymentDashboard = computed(() => ["admin", "management", "finance"].includes(myRole.value));
const canSeeManageVendors = computed(() => ["admin", "management"].includes(myRole.value));
const canCreateVendor = computed(() => myRole.value === "admin");
const canSeeManageTeam = computed(() => myRole.value === "admin");

const SIDEBAR_BRAND = {
  admin: "Admin Console", management: "Management Console",
  operations: "Operations Portal", finance: "Finance Portal",
};
const sidebarBrand = computed(() => SIDEBAR_BRAND[myRole.value] || "Admin Console");
const ROLE_FALLBACK_NAME = { admin: "Admin", management: "Management", operations: "Operations", finance: "Finance" };

const navItems = computed(() => {
  const items = [];
  if (canSeePoTracking.value) items.push({ id: "po-tracking", label: "PO Tracking" });
  if (canSeeSkuData.value) items.push({ id: "sku-data", label: "SKU Level Data" });
  if (canSeePaymentDashboard.value) items.push({ id: "payment-dashboard", label: "Payment Dashboard" });
  if (canSeeManageVendors.value) items.push({ id: "manage-vendors", label: "Manage Vendors" });
  if (canSeeManageTeam.value) items.push({ id: "manage-team", label: "Manage Team" });
  return items;
});

const activeNav = ref("po-tracking");
const pageTitle = computed(() => ({
  "po-tracking": "PO Tracking",
  "sku-data": "SKU Level Data",
  "payment-dashboard": "Payment Dashboard",
  "manage-vendors": "Manage Vendors",
  "manage-team": "Manage Team",
}[activeNav.value]));

const { currentPos, grnsByPo, poItemsByPo, grnItemsByPoSku, grnByCode, lastUpdated, invoicesForItem } = usePurchaseOrders();
const { vendors, refresh: refreshVendors, vendorLabel, revokeVendor, restoreVendor, deleteVendor } = useVendors();
const { team, refresh: refreshTeam, revokeTeamMember, restoreTeamMember, deleteTeamMember } = useTeam();
const { filters, filteredSorted, facilityOptions, statusOptions } = usePoFilters(currentPos, grnsByPo, vendorLabel);
const { sortedRows: skuRows } = useSkuAggregates(currentPos, poItemsByPo, { multiVendor: true });

// Two logins can share the same vendor_code (e.g. a second Lexcru user) --
// dedupe by vendor_code so the filter dropdown lists each vendor once,
// not once per login.
const vendorOptions = computed(() => {
  const byCode = new Map();
  for (const v of vendors.value) {
    if (!v.vendor_code || byCode.has(v.vendor_code)) continue;
    byCode.set(v.vendor_code, { code: v.vendor_code, label: v.vendor_name || v.vendor_code });
  }
  return [...byCode.values()];
});
const { filters: skuFilters, filteredSorted: skuFilteredSorted } = useSkuFilters(skuRows, vendorLabel);
const { allUploads, fetchAllUploads } = useInvoiceUploads();
const { filters: paymentFilters, filteredSorted: paymentFilteredSorted, reconciliationOptions } = usePaymentFilters(allUploads, vendorLabel);

const scopeLine = computed(() => {
  const total = currentPos.value.length;
  const shown = filteredSorted.value.length;
  return `${total} purchase order${total === 1 ? "" : "s"} across all vendors` +
    (shown !== total ? ` · showing ${shown} matching current filters` : "");
});
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
    po, items, invoices, vendorLabelText: vendorLabel(po.vendor_code, po.vendor_name),
    allowInvoiceUpload: true, uploaderLabel: whoLine.value,
  }, poCode);
}

function openSkuDetailModal(key) {
  const found = skuRows.value.find(a => a.key === key);
  if (!found) return;
  openModal(found.item_name || found.item_sku, SkuDetailModal, {
    agg: found, onOpenPo: openPoDetailModal, vendorLabelText: vendorLabel(found.vendor_code, found.vendor_name),
  }, `(${found.item_sku})`);
}

onMounted(async () => {
  const ctx = await requireSession();
  if (!ctx) return;
  if (!INTERNAL_ROLES.has(ctx.profile.role)) {
    window.location.href = "vendor.html";
    return;
  }
  myRole.value = ctx.profile.role;
  activeNav.value = navItems.value[0]?.id || "po-tracking";
  whoLine.value = ctx.profile.vendor_name || ROLE_FALLBACK_NAME[myRole.value] || "Admin";
  myEmail.value = ctx.profile.email || "";
  // vendorLabel()/vendorOptions (built from `vendors`) feed every
  // multi-vendor view (PO Tracking, SKU Data, Payment Dashboard), not just
  // Manage Vendors -- so this loads for every role, unlike the team roster
  // below, which only Manage Team (admin-only) ever shows.
  await refreshVendors();
  if (canSeeManageTeam.value) await refreshTeam();
  await fetchAllUploads();
  ready.value = true;
});

async function signOut() {
  await supabase.auth.signOut();
  window.location.href = "login.html";
}
</script>

<template>
  <div v-if="ready" class="app-shell">
    <SidebarNav v-model="activeNav" :brand="sidebarBrand" :items="navItems" />

    <div class="main-content">
      <div class="wrap">
        <header class="page-head">
          <div>
            <h1>{{ pageTitle }}</h1>
            <div class="scope">
              <template v-if="activeNav === 'po-tracking'">{{ scopeLine }}</template>
              <template v-else-if="activeNav === 'sku-data'">SKUs with at least one open purchase order not yet fully supplied, highest pending quantity first, across all vendors. Click a SKU for the PO-level breakdown.</template>
              <template v-else-if="activeNav === 'payment-dashboard'">Every invoice uploaded across all vendors, with its reconciliation and payment status. Click a PO to see its details.</template>
              <template v-else-if="activeNav === 'manage-team'">Create and manage internal logins for Management, Operations and Finance access.</template>
            </div>
          </div>
          <div class="who">
            <ProfileMenu :display-name="whoLine" :email="myEmail" :on-sign-out="signOut" />
          </div>
        </header>

        <div v-if="canSeePoTracking" v-show="activeNav === 'po-tracking'">
          <PoTrackingTable
            :rows="filteredSorted" :filters="filters"
            :facility-options="facilityOptions" :status-options="statusOptions"
            :vendor-options="vendorOptions" :vendor-label="vendorLabel"
            :grns-by-po="grnsByPo" :show-kpis="false"
            :on-open-po="openPoDetailModal" :allow-invoice-upload="true" :uploader-label="whoLine"
          />
        </div>

        <div v-if="canSeeSkuData" v-show="activeNav === 'sku-data'">
          <SkuLevelTable
            :rows="skuFilteredSorted" :filters="skuFilters"
            :vendor-options="vendorOptions" :vendor-label="vendorLabel"
            :on-open-sku="openSkuDetailModal"
          />
        </div>

        <div v-if="canSeePaymentDashboard" v-show="activeNav === 'payment-dashboard'">
          <PaymentDashboardTable
            :rows="paymentFilteredSorted" :filters="paymentFilters" :reconciliation-options="reconciliationOptions"
            :vendor-options="vendorOptions" :vendor-label="vendorLabel"
            :on-open-po="openPoDetailModal"
          />
        </div>

        <div v-if="canSeeManageVendors" v-show="activeNav === 'manage-vendors'">
          <ManageVendors
            :vendors="vendors" :on-vendors-changed="refreshVendors" :allow-create="canCreateVendor"
            :on-revoke="revokeVendor" :on-restore="restoreVendor" :on-delete="deleteVendor"
          />
        </div>

        <div v-if="canSeeManageTeam" v-show="activeNav === 'manage-team'">
          <ManageTeam
            :team="team" :on-team-changed="refreshTeam"
            :on-revoke="revokeTeamMember" :on-restore="restoreTeamMember" :on-delete="deleteTeamMember"
          />
        </div>

        <footer class="page-foot">Data refreshes automatically every ~5 minutes from Uniware. {{ lastCheckedText }}</footer>
      </div>
    </div>

    <AppModal />
  </div>
</template>
