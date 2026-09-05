<script setup>
import { ref, computed, onMounted } from "vue";
import { supabase, requireSession } from "./supabaseClient.js";
import { usePurchaseOrders } from "./composables/usePurchaseOrders.js";
import { usePoFilters } from "./composables/usePoFilters.js";
import { useSkuAggregates } from "./composables/useSkuAggregates.js";
import { useVendors } from "./composables/useVendors.js";
import { useModal } from "./composables/useModal.js";
import SidebarNav from "./components/SidebarNav.vue";
import PoTrackingTable from "./components/PoTrackingTable.vue";
import SkuLevelTable from "./components/SkuLevelTable.vue";
import ManageVendors from "./components/ManageVendors.vue";
import AppModal from "./components/AppModal.vue";
import PoDetailModal from "./components/PoDetailModal.vue";
import SkuDetailModal from "./components/SkuDetailModal.vue";
import ProfileMenu from "./components/ProfileMenu.vue";

const ready = ref(false);
const whoLine = ref("Admin");
const myEmail = ref("");
const activeNav = ref("po-tracking");
const pageTitle = computed(() => ({
  "po-tracking": "PO Tracking",
  "sku-data": "SKU Level Data",
  "manage-vendors": "Manage Vendors",
}[activeNav.value]));

const { currentPos, grnsByPo, poItemsByPo, grnItemsByPoSku, grnByCode, lastUpdated, invoicesForItem } = usePurchaseOrders();
const { vendors, refresh: refreshVendors, vendorLabel, revokeVendor, restoreVendor, deleteVendor } = useVendors();
const { filters, filteredSorted, facilityOptions, statusOptions } = usePoFilters(currentPos, grnsByPo, vendorLabel);
const { sortedRows: skuRows } = useSkuAggregates(currentPos, poItemsByPo, { multiVendor: true });

const vendorOptions = computed(() => vendors.value.map(v => ({ code: v.vendor_code, label: v.vendor_name || v.vendor_code })));

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
  const invoices = [...new Set(grns.map(g => g.vendor_invoice_number).filter(Boolean))];
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
  if (ctx.profile.role !== "admin") {
    window.location.href = "vendor.html";
    return;
  }
  whoLine.value = ctx.profile.vendor_name || "Admin";
  myEmail.value = ctx.profile.email || "";
  await refreshVendors();
  ready.value = true;
});

async function signOut() {
  await supabase.auth.signOut();
  window.location.href = "login.html";
}
</script>

<template>
  <div v-if="ready" class="app-shell">
    <SidebarNav
      v-model="activeNav"
      brand="Admin Console"
      :items="[
        { id: 'po-tracking', label: 'PO Tracking' },
        { id: 'sku-data', label: 'SKU Level Data' },
        { id: 'manage-vendors', label: 'Manage Vendors' },
      ]"
    />

    <div class="main-content">
      <div class="wrap">
        <header class="page-head">
          <div>
            <h1>{{ pageTitle }}</h1>
            <div class="scope">
              <template v-if="activeNav === 'po-tracking'">{{ scopeLine }}</template>
              <template v-else-if="activeNav === 'sku-data'">SKUs with at least one open purchase order not yet fully supplied, highest pending quantity first, across all vendors. Click a SKU for the PO-level breakdown.</template>
            </div>
          </div>
          <div class="who">
            <ProfileMenu :display-name="whoLine" :email="myEmail" :on-sign-out="signOut" />
          </div>
        </header>

        <div v-show="activeNav === 'po-tracking'">
          <PoTrackingTable
            :rows="filteredSorted" :filters="filters"
            :facility-options="facilityOptions" :status-options="statusOptions"
            :vendor-options="vendorOptions" :vendor-label="vendorLabel"
            :grns-by-po="grnsByPo" :show-kpis="false"
            :on-open-po="openPoDetailModal" :allow-invoice-upload="true" :uploader-label="whoLine"
          />
        </div>

        <div v-show="activeNav === 'sku-data'">
          <SkuLevelTable
            :rows="skuRows" :show-vendor-column="true" :vendor-label="vendorLabel"
            :on-open-sku="openSkuDetailModal"
          />
        </div>

        <div v-show="activeNav === 'manage-vendors'">
          <ManageVendors
            :vendors="vendors" :on-vendors-changed="refreshVendors"
            :on-revoke="revokeVendor" :on-restore="restoreVendor" :on-delete="deleteVendor"
          />
        </div>

        <footer class="page-foot">Data refreshes automatically every ~5 minutes from Uniware. {{ lastCheckedText }}</footer>
      </div>
    </div>

    <AppModal />
  </div>
</template>
