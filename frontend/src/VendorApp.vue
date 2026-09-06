<script setup>
import { ref, computed, onMounted } from "vue";
import { supabase, requireSession } from "./supabaseClient.js";
import { usePurchaseOrders } from "./composables/usePurchaseOrders.js";
import { usePoFilters } from "./composables/usePoFilters.js";
import { useSkuAggregates } from "./composables/useSkuAggregates.js";
import { useSkuFilters } from "./composables/useSkuFilters.js";
import { useModal } from "./composables/useModal.js";
import { useInvoiceUploads } from "./composables/useInvoiceUploads.js";
import { usePaymentFilters } from "./composables/usePaymentFilters.js";
import { dedupeInvoiceNumbers } from "./format.js";
import SidebarNav from "./components/SidebarNav.vue";
import PoTrackingTable from "./components/PoTrackingTable.vue";
import SkuLevelTable from "./components/SkuLevelTable.vue";
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
const activeNav = ref("po-tracking");
const pageTitle = computed(() => ({
  "po-tracking": "PO Tracking",
  "sku-data": "SKU Level Data",
  "payment-dashboard": "Payment Dashboard",
}[activeNav.value]));

const { currentPos, grnsByPo, poItemsByPo, grnItemsByPoSku, grnByCode, lastUpdated, invoicesForItem } = usePurchaseOrders();
const { filters, filteredSorted, facilityOptions, statusOptions } = usePoFilters(currentPos, grnsByPo);
const { sortedRows: skuRows } = useSkuAggregates(currentPos, poItemsByPo, { multiVendor: false });
const { filters: skuFilters, filteredSorted: skuFilteredSorted } = useSkuFilters(skuRows);
const { allUploads, fetchAllUploads } = useInvoiceUploads();
const { filters: paymentFilters, filteredSorted: paymentFilteredSorted, reconciliationOptions } = usePaymentFilters(allUploads);

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
    po, items, invoices, allowInvoiceUpload: true, uploaderLabel: myDisplayName.value,
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
  if (ctx.profile.must_change_password) {
    mustChangePassword.value = true;
    return;
  }
  myDisplayName.value = ctx.profile.vendor_name || ctx.profile.email || "Vendor";
  myEmail.value = ctx.profile.email || "";
  await fetchAllUploads();
  ready.value = true;
});

async function handlePasswordChanged() {
  // Re-fetch so we pick up the freshly-cleared must_change_password and the
  // profile fields the dashboard needs, rather than trusting stale state.
  const ctx = await requireSession();
  if (!ctx) return;
  mustChangePassword.value = false;
  myDisplayName.value = ctx.profile.vendor_name || ctx.profile.email || "Vendor";
  myEmail.value = ctx.profile.email || "";
  ready.value = true;
}

async function signOut() {
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
        { id: 'po-tracking', label: 'PO Tracking' },
        { id: 'sku-data', label: 'SKU Level Data' },
        { id: 'payment-dashboard', label: 'Payment Dashboard' },
      ]"
    />

    <div class="main-content">
      <div class="wrap">
        <header class="page-head">
          <div>
            <h1>{{ pageTitle }}</h1>
            <div class="scope">
              <template v-if="activeNav === 'po-tracking'">{{ scopeLine }}</template>
              <template v-else-if="activeNav === 'sku-data'">SKUs with at least one open purchase order not yet fully supplied, highest pending quantity first. Click a SKU for the PO-level breakdown.</template>
              <template v-else-if="activeNav === 'payment-dashboard'">Every invoice you've uploaded, with its reconciliation and payment status. Click a PO to see its details.</template>
            </div>
          </div>
          <div class="who">
            <ProfileMenu :display-name="myDisplayName" :email="myEmail" :on-sign-out="signOut" />
          </div>
        </header>

        <div v-show="activeNav === 'po-tracking'">
          <PoTrackingTable
            :rows="filteredSorted" :filters="filters"
            :facility-options="facilityOptions" :status-options="statusOptions"
            :grns-by-po="grnsByPo" :show-kpis="true"
            :on-open-po="openPoDetailModal" :allow-invoice-upload="true" :uploader-label="myDisplayName"
          />
        </div>

        <div v-show="activeNav === 'sku-data'">
          <SkuLevelTable :rows="skuFilteredSorted" :filters="skuFilters" :on-open-sku="openSkuDetailModal" />
        </div>

        <div v-show="activeNav === 'payment-dashboard'">
          <PaymentDashboardTable
            :rows="paymentFilteredSorted" :filters="paymentFilters" :reconciliation-options="reconciliationOptions"
            :on-open-po="openPoDetailModal"
          />
        </div>

        <footer class="page-foot">Data refreshes automatically every ~5 minutes from Uniware. {{ lastCheckedText }}</footer>
      </div>
    </div>

    <AppModal />
  </div>
</template>
