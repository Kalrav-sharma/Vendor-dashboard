<script setup>
import { ref, computed, onMounted } from "vue";
import { supabase, requireSession } from "./supabaseClient.js";
import { usePurchaseOrders } from "./composables/usePurchaseOrders.js";
import { usePoFilters } from "./composables/usePoFilters.js";
import { useSkuAggregates } from "./composables/useSkuAggregates.js";
import { useModal } from "./composables/useModal.js";
import SidebarNav from "./components/SidebarNav.vue";
import PoTrackingTable from "./components/PoTrackingTable.vue";
import SkuLevelTable from "./components/SkuLevelTable.vue";
import AppModal from "./components/AppModal.vue";
import PoDetailModal from "./components/PoDetailModal.vue";
import SkuDetailModal from "./components/SkuDetailModal.vue";

const ready = ref(false);
const activeNav = ref("po-tracking");
const pageTitle = computed(() => activeNav.value === "po-tracking" ? "PO Tracking" : "SKU Level Data");

const { currentPos, grnsByPo, poItemsByPo, grnItemsByPoSku, grnByCode, lastUpdated, invoicesForItem } = usePurchaseOrders();
const { filters, filteredSorted, facilityOptions, statusOptions } = usePoFilters(currentPos, grnsByPo);
const { sortedRows: skuRows } = useSkuAggregates(currentPos, poItemsByPo, { multiVendor: false });

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
  const invoices = [...new Set(grns.map(g => g.vendor_invoice_number).filter(Boolean))];
  openModal("Purchase Order", PoDetailModal, { po, items, invoices, allowInvoiceUpload: true }, poCode);
}

function openSkuDetailModal(key) {
  const found = skuRows.value.find(a => a.key === key);
  if (!found) return;
  openModal(found.item_name || found.item_sku, SkuDetailModal, { agg: found, onOpenPo: openPoDetailModal }, `(${found.item_sku})`);
}

onMounted(async () => {
  const ctx = await requireSession();
  if (!ctx) return;
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
      brand="Vendor Portal"
      :items="[{ id: 'po-tracking', label: 'PO Tracking' }, { id: 'sku-data', label: 'SKU Level Data' }]"
    />

    <div class="main-content">
      <div class="wrap">
        <header class="page-head">
          <div>
            <h1>{{ pageTitle }}</h1>
            <div class="scope">{{ activeNav === 'po-tracking' ? scopeLine : "SKUs with at least one open purchase order not yet fully supplied, highest pending quantity first. Click a SKU for the PO-level breakdown." }}</div>
          </div>
          <div class="who">
            <button class="link-btn" @click="signOut">Sign out</button>
          </div>
        </header>

        <div v-show="activeNav === 'po-tracking'">
          <PoTrackingTable
            :rows="filteredSorted" :filters="filters"
            :facility-options="facilityOptions" :status-options="statusOptions"
            :grns-by-po="grnsByPo" :show-kpis="true"
            :on-open-po="openPoDetailModal"
          />
        </div>

        <div v-show="activeNav === 'sku-data'">
          <SkuLevelTable :rows="skuRows" :on-open-sku="openSkuDetailModal" />
        </div>

        <footer class="page-foot">Data refreshes automatically every ~5 minutes from Uniware. {{ lastCheckedText }}</footer>
      </div>
    </div>

    <AppModal />
  </div>
</template>
