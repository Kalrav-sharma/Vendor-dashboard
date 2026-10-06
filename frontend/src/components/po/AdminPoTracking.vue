<script setup>
// Admin console › PO Tracking, layered top to bottom: KPI tiles, three clickable breakdown
// cards (open supply by age / by facility / top vendors), lifecycle-stage tabs, then the PO
// table itself. Admin-only -- the vendor portal keeps PoTrackingTable.vue unchanged.
//
// `rows` arrives already narrowed by the toolbar's search/vendor/facility/status filters
// (usePoFilters.js); the age filter and stage tab are local to this page and narrow further.
import { computed, onMounted, ref, watch } from "vue";
import StatusChip from "../StatusChip.vue";
import SummaryKpis from "../SummaryKpis.vue";
import InvoiceUploadModal from "../InvoiceUploadModal.vue";
import InvoiceUploadButton from "../InvoiceUploadButton.vue";
import GrnStageDialog from "./GrnStageDialog.vue";
import { useInvoiceUploads } from "../../composables/useInvoiceUploads.js";
import { useVendorRequests, isRequestAnswered, REQUEST_KIND_META } from "../../composables/useVendorRequests.js";
import { downloadCsv } from "../sla/slaUtil.js";
import {
  fmtNum, fmtMoney, fmtMoneyCompact, fmtDateOnly, statusLabel, TERMINAL_STATUSES, poSortComparator, dedupeInvoiceNumbers,
} from "../../format.js";
import "../payments/payments.css";
import "./po-tracking.css";

const props = defineProps({
  rows: { type: Array, required: true },        // already filtered + sorted by usePoFilters
  filters: { type: Object, required: true },     // usePoFilters' reactive state, mutated directly
  facilityOptions: { type: Array, required: true },
  statusOptions: { type: Array, required: true },
  vendorOptions: { type: Array, required: true }, // [{code, label}]
  vendorLabel: { type: Function, required: true },
  grnsByPo: { type: Object, required: true },
  grnItemsByPoSku: { type: Object, required: true }, // "<po>|<sku>" -> grn_items rows
  onOpenPo: { type: Function, required: true },
  uploaderLabel: { type: String, default: "" }, // current user's display name -- recorded on uploads and vendor requests
  canChangeStage: { type: Boolean, default: false }, // may raise/withdraw vendor requests from GRN pending
});

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const DAY_MS = 24 * 60 * 60 * 1000;

// Invoice-upload rows (payment_status/match_status only) for every PO in view -- same bulk
// fetch PoTrackingTable uses; the stage of a PO past receipt depends on them.
const { uploadsByPo, fetchUploadCounts } = useInvoiceUploads();
watch(() => props.rows, (rows) => fetchUploadCounts(rows.map((p) => p.po_code)), { immediate: true });

// Credit-note / corrected-invoice requests the team has sent vendors (useVendorRequests.js).
const { latestByUpload, fetchRequests, withdrawRequest } = useVendorRequests();
onMounted(() => fetchRequests());
// An unpaid upload's request, and whether the vendor has answered it yet.
function requestOf(u, uploads) {
  const req = u.payment_status === "paid" ? null : latestByUpload.value[u.id];
  return req ? { req, answered: isRequestAnswered(req, uploads) } : null;
}
// Requests still waiting on the vendor, for a PO's "With vendor" stage.
function openRequests(p) {
  const uploads = uploadsByPo[p.po_code] || [];
  return uploads.map((u) => requestOf(u, uploads)).filter((r) => r && !r.answered).map((r) => r.req);
}

function grnInfo(poCode) {
  const grns = props.grnsByPo[poCode] || [];
  const invoices = dedupeInvoiceNumbers(grns.map((g) => g.vendor_invoice_number));
  const recv = grns.reduce((s, g) => s + (Number(g.total_received_amount) || 0), 0);
  const rej = grns.reduce((s, g) => s + (Number(g.total_rejected_amount) || 0), 0);
  return { raised: grns.length > 0, invoices, recv, rej };
}

// --- GRN pending: invoiced qty the warehouse hasn't receipted yet -------------------------
// Judged live, not from the stored match_status: an invoice checked before its GRN existed
// stays "needs_review" forever (the daily re-check only re-runs mismatches), so instead each
// unpaid upload's OCR'd qty is compared against the received qty on GRNs carrying the same
// invoice number -- the same punctuation/case-insensitive match check-invoice-match uses.
const normalizeCode = (s) => (s || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
const grnQtyByCode = computed(() => {
  const m = {};
  for (const items of Object.values(props.grnItemsByPoSku)) {
    for (const gi of items) m[gi.grn_code] = (m[gi.grn_code] || 0) + (Number(gi.quantity) || 0);
  }
  return m;
});
// Bulk-fetched rows carry invoice_number/line_quantities directly; a row re-fetched in full
// (after an upload or Re-check in the PO modal) carries them inside match_details instead.
function invoiceFields(u) {
  const ex = u.match_details?.extracted;
  return { number: u.invoice_number ?? ex?.invoice_number, qtys: u.line_quantities ?? ex?.line_quantities };
}
// An invoice with a request on it leaves the gap: an open request puts the PO in "With
// vendor" instead, and an answered one (credit note in, or a corrected invoice uploaded --
// which is then judged on its own) settles that invoice's shortfall.
function grnGap(p) {
  const grns = props.grnsByPo[p.po_code] || [];
  const uploads = uploadsByPo[p.po_code] || [];
  let invoiced = 0, received = 0;
  const shortInvoices = [];
  const shortUploads = [];
  for (const u of uploads) {
    if (u.payment_status === "paid" || requestOf(u, uploads)) continue;
    const { number, qtys } = invoiceFields(u);
    const invQty = (Array.isArray(qtys) ? qtys : []).reduce((s, q) => s + (Number(q) >= 0 ? Number(q) : 0), 0);
    if (!number || !(invQty > 0)) continue; // OCR couldn't read it -- nothing to judge against
    const key = normalizeCode(number);
    const grnQty = grns.filter((g) => key && normalizeCode(g.vendor_invoice_number) === key)
      .reduce((s, g) => s + (grnQtyByCode.value[g.grn_code] || 0), 0);
    if (grnQty < invQty - 0.01) {
      invoiced += invQty; received += grnQty;
      shortInvoices.push(number);
      shortUploads.push({ upload: u, number, invQty, grnQty });
    }
  }
  return { pending: shortInvoices.length > 0, invoiced, received, invoices: shortInvoices, shortUploads };
}

// --- Lifecycle stage --------------------------------------------------------------------
// One stage per PO, checked in order, so every PO sits in exactly one tab:
//   Cancelled (or closed in Uniware with nothing ever received) -> closed
//   an unpaid uploaded invoice's qty exceeds its GRN'd qty      -> grn_pending (see grnGap)
//   the team has asked the vendor for a CN / corrected invoice  -> with_vendor, until answered
//   no GRN raised yet                                           -> awaiting_grn
//   GRN raised but short of the ordered qty, PO still open      -> part_received
//   received, no invoice uploaded via the portal:
//     Finance's payout file shows it paid -> closed, booked -> with_finance, else invoice_pending
//   invoice(s) uploaded: all paid -> closed, otherwise with_finance
// "Paid" is the same payment_status === "paid" check the vendor side's PO complete bucket uses.
const STAGES = [
  { key: "awaiting_grn", label: "Awaiting supply", cls: "muted", hint: "No GRN raised yet" },
  { key: "grn_pending", label: "GRN pending", cls: "accent", hint: "Invoice uploaded, GRN qty short of invoiced qty" },
  { key: "with_vendor", label: "With vendor", cls: "vendor", hint: "Credit note or corrected invoice requested from the vendor" },
  { key: "part_received", label: "Part-received", cls: "open", hint: "GRN raised, short of ordered qty" },
  { key: "invoice_pending", label: "Invoice pending", cls: "critical", hint: "Received, no invoice uploaded" },
  { key: "with_finance", label: "With Finance", cls: "info", hint: "Invoice in, not yet paid" },
  { key: "closed", label: "Paid / closed", cls: "good", hint: "Paid, or cancelled" },
];
const STAGE_BY_KEY = Object.fromEntries(STAGES.map((s) => [s.key, s]));
const SUPPLY_OPEN = new Set(["awaiting_grn", "part_received"]);

function stageOf(p) {
  const hasGrn = (props.grnsByPo[p.po_code] || []).length > 0;
  if (p.status === "CANCELLED" || (TERMINAL_STATUSES.has(p.status) && !hasGrn)) return "closed";
  if (grnGap(p).pending) return "grn_pending";
  if (openRequests(p).length) return "with_vendor";
  if (!hasGrn) return "awaiting_grn";
  if (!TERMINAL_STATUSES.has(p.status) && Number(p.qty_received) < Number(p.qty_ordered)) return "part_received";
  const uploads = uploadsByPo[p.po_code] || [];
  if (!uploads.length) {
    if (p.payment_status === "paid") return "closed";
    return p.payment_status ? "with_finance" : "invoice_pending";
  }
  return uploads.every((u) => u.payment_status === "paid") ? "closed" : "with_finance";
}
function stageLabel(p, stage) {
  if (stage === "with_vendor") return REQUEST_KIND_META[openRequests(p)[0]?.kind]?.short || STAGE_BY_KEY[stage].label;
  return stage === "closed" && p.status === "CANCELLED" ? "Cancelled" : STAGE_BY_KEY[stage].label;
}

// --- Change stage (GRN pending -> With vendor) and withdraw -------------------------------
const stageDialogPo = ref(null);
const stageDialogUploads = computed(() => (stageDialogPo.value ? grnGap(stageDialogPo.value).shortUploads : []));
const ago = (t) => {
  const d = Math.floor((Date.now() - new Date(t)) / DAY_MS);
  return d <= 0 ? "today" : d === 1 ? "yesterday" : `${d}d ago`;
};
const withdrawError = ref("");
async function withdraw(req) {
  if (!window.confirm(`Withdraw this ${REQUEST_KIND_META[req.kind].short.toLowerCase()}? The vendor will stop seeing it and the PO goes back to GRN pending.`)) return;
  const res = await withdrawRequest(req, props.uploaderLabel);
  withdrawError.value = res.ok ? "" : `Couldn't withdraw: ${res.error}`;
}

// The 3-step breakdown under a "With Finance" chip -- same checks as the vendor side.
function financeSteps(p) {
  const uploads = uploadsByPo[p.po_code] || [];
  const grn = Number(p.qty_ordered) > 0 && Number(p.qty_received) >= Number(p.qty_ordered);
  const recon = uploads.length > 0 && uploads.every((u) => u.match_status === "matched");
  const booked = uploads.length > 0 ? uploads.every((u) => !!u.payment_status) : !!p.payment_status;
  return [
    { ok: grn, tip: grn ? "GRN Complete" : "GRN Pending" },
    { ok: recon, tip: recon ? "Reconciliation Complete" : uploads.length ? "Reconciliation Pending" : "No portal invoice" },
    { ok: booked, tip: booked ? "Payment Booked" : "Payment Not Booked" },
  ];
}

const ageDays = (p) => (p.created_at ? Math.max(0, Math.floor((Date.now() - new Date(p.created_at)) / DAY_MS)) : null);
const fillPct = (p) => (Number(p.qty_ordered) > 0 ? Math.min(100, (Number(p.qty_received) / Number(p.qty_ordered)) * 100) : 0);

// Every PO in view, decorated once so the panels, tabs and table all read the same numbers.
const decorated = computed(() => props.rows.map((p) => {
  const stage = stageOf(p);
  return { p, stage, age: ageDays(p), fill: fillPct(p), value: Number(p.total_amount) || 0 };
}));

// --- Age filter (local) -----------------------------------------------------------------
const AGE_BUCKETS = [
  { key: "0-7", label: "0–7 days", min: 0, max: 7 },
  { key: "8-30", label: "8–30 days", min: 8, max: 30 },
  { key: "31-60", label: "31–60 days", min: 31, max: 60, cls: "open" },
  { key: "60+", label: "60+ days", min: 61, max: Infinity, cls: "critical" },
];
const ageFilter = ref("");
const inAge = (d, key) => {
  const b = AGE_BUCKETS.find((x) => x.key === key);
  return !b || (d.age != null && d.age >= b.min && d.age <= b.max);
};
const scoped = computed(() => decorated.value.filter((d) => inAge(d, ageFilter.value)));
const supplyOpen = computed(() => scoped.value.filter((d) => SUPPLY_OPEN.has(d.stage)));

// --- KPI tiles --------------------------------------------------------------------------
const kpiTiles = computed(() => {
  const all = scoped.value;
  const open = supplyOpen.value;
  const sumValue = (l) => l.reduce((s, d) => s + d.value, 0);
  const aged = open.filter((d) => d.age != null && d.age > 30);
  const live = all.filter((d) => d.p.status !== "CANCELLED");
  const ordered = live.reduce((s, d) => s + (Number(d.p.qty_ordered) || 0), 0);
  const received = live.reduce((s, d) => s + (Number(d.p.qty_received) || 0), 0);
  const invPending = all.filter((d) => d.stage === "invoice_pending");
  const withFin = all.filter((d) => d.stage === "with_finance");
  const unitsPending = open.reduce((s, d) => s + Math.max(0, (Number(d.p.qty_ordered) || 0) - (Number(d.p.qty_received) || 0)), 0);
  return [
    { label: "Purchase orders", value: fmtNum(all.length), sublabel: `${fmtMoneyCompact(sumValue(all))} PO value` },
    { label: "Open supply", value: fmtNum(open.length), sublabel: `${fmtNum(unitsPending)} units · ${fmtMoneyCompact(sumValue(open))}` },
    {
      label: "Open 30+ days", value: fmtNum(aged.length), cls: aged.length ? "critical" : "",
      sublabel: aged.length ? `oldest ${Math.max(...aged.map((d) => d.age))}d · ${fmtMoneyCompact(sumValue(aged))}` : "Nothing aging",
    },
    { label: "Fill rate", value: ordered ? `${Math.round((received / ordered) * 100)}%` : "–", sublabel: `${fmtNum(received)} of ${fmtNum(ordered)} units` },
    {
      label: "Invoice pending", value: fmtNum(invPending.length), cls: invPending.length ? "critical" : "",
      sublabel: `${fmtMoneyCompact(invPending.reduce((s, d) => s + grnInfo(d.p.po_code).recv, 0))} GRN value`,
    },
    { label: "With Finance", value: fmtNum(withFin.length), sublabel: `${fmtMoneyCompact(sumValue(withFin))} PO value` },
  ];
});

// --- Breakdown cards (click a bar to filter) ---------------------------------------------
function group(list, keyOf) {
  const m = new Map();
  for (const d of list) {
    const k = keyOf(d);
    const g = m.get(k) || { key: k, count: 0, amount: 0 };
    g.count++; g.amount += d.value;
    m.set(k, g);
  }
  return m;
}
// Age card reads every PO in view (ignoring the age filter itself) so all four buckets stay
// clickable; the facility/vendor cards follow the age filter like everything else.
const ageCard = computed(() => {
  const open = decorated.value.filter((d) => SUPPLY_OPEN.has(d.stage));
  const m = group(open, (d) => AGE_BUCKETS.find((b) => d.age != null && d.age >= b.min && d.age <= b.max)?.key);
  return AGE_BUCKETS.map((b) => ({ ...b, count: m.get(b.key)?.count || 0, amount: m.get(b.key)?.amount || 0 }));
});
const facilityCard = computed(() => [...group(supplyOpen.value, (d) => d.p.facility || "–").values()]
  .map((g) => ({ ...g, label: g.key }))
  .sort((a, b) => b.amount - a.amount).slice(0, 6));
const vendorCard = computed(() => [...group(supplyOpen.value, (d) => d.p.vendor_code).values()]
  .map((g) => ({ ...g, label: props.vendorLabel(g.key) }))
  .sort((a, b) => b.amount - a.amount).slice(0, 6));

const cards = computed(() => [
  { id: "age", title: "Open supply by age", sub: "since PO created", buckets: ageCard.value, selected: ageFilter.value },
  { id: "facility", title: "Open supply by facility", sub: "top 6 by value", buckets: facilityCard.value, selected: props.filters.facility },
  { id: "vendor", title: "Open supply by vendor", sub: "top 6 by value", buckets: vendorCard.value, selected: props.filters.vendor },
]);
function pickBucket(cardId, key) {
  if (cardId === "age") ageFilter.value = ageFilter.value === key ? "" : key;
  else props.filters[cardId] = props.filters[cardId] === key ? "" : key;
}
const barPct = (buckets, b) => {
  const max = Math.max(...buckets.map((x) => x.amount), 0);
  return max > 0 ? Math.max(b.amount > 0 ? 1.5 : 0, (b.amount / max) * 100) : 0;
};

// --- Stage tabs + sort ------------------------------------------------------------------
const activeStage = ref("all");
const stageCounts = computed(() => {
  const c = { all: scoped.value.length };
  for (const s of STAGES) c[s.key] = 0;
  for (const d of scoped.value) c[d.stage]++;
  return c;
});

const SORTS = {
  age: (a, b) => (a.age ?? -1) - (b.age ?? -1),
  fill: (a, b) => a.fill - b.fill,
  value: (a, b) => a.value - b.value,
};
const sort = ref({ key: null, dir: -1 }); // null = default: open first, then value desc
function toggleSort(key) {
  sort.value = sort.value.key !== key ? { key, dir: -1 } : sort.value.dir === -1 ? { key, dir: 1 } : { key: null, dir: -1 };
}
const sortMark = (key) => (sort.value.key !== key ? "" : sort.value.dir === -1 ? " ↓" : " ↑");

const tableRows = computed(() => {
  const list = activeStage.value === "all" ? scoped.value : scoped.value.filter((d) => d.stage === activeStage.value);
  const { key, dir } = sort.value;
  return key ? [...list].sort((a, b) => dir * SORTS[key](a, b)) : [...list].sort((a, b) => poSortComparator(a.p, b.p));
});
const tableValue = computed(() => tableRows.value.reduce((s, d) => s + d.value, 0));

const hasLocalFilters = computed(() => !!(ageFilter.value || props.filters.search || props.filters.vendor || props.filters.facility || props.filters.status));
function clearFilters() {
  ageFilter.value = "";
  for (const k of Object.keys(props.filters)) props.filters[k] = "";
}

// Invoice upload count chip -- GRN invoice numbers vs PDFs actually uploaded.
function uploadStatus(poCode) {
  const expected = grnInfo(poCode).invoices.length;
  const uploaded = (uploadsByPo[poCode] || []).length;
  return { expected, uploaded, pending: expected > 0 && uploaded < expected };
}

function exportCsv() {
  const headers = ["Vendor", "PO code", "Facility", "Created", "Age (days)", "Stage", "Uniware status",
    "Qty ordered", "Qty received", "Fill %", "PO value", "GRN invoice numbers", "GRN received value", "Invoices uploaded", "Invoiced qty not yet GRN'd"];
  const out = tableRows.value.map((d) => {
    const g = grnInfo(d.p.po_code);
    return [props.vendorLabel(d.p.vendor_code, d.p.vendor_name), d.p.po_code, d.p.facility, fmtDateOnly(d.p.created_at), d.age ?? "",
      stageLabel(d.p, d.stage), statusLabel(d.p.status), d.p.qty_ordered ?? "", d.p.qty_received ?? "", Math.round(d.fill),
      d.p.total_amount ?? "", g.invoices.join(" | "), g.recv, (uploadsByPo[d.p.po_code] || []).length,
      d.stage === "grn_pending" ? grnGap(d.p).invoiced - grnGap(d.p).received : ""];
  });
  const tab = activeStage.value === "all" ? "all" : activeStage.value;
  downloadCsv(`po_tracking_${tab}_${new Date().toISOString().slice(0, 10)}.csv`, headers, out);
}

// One shared upload popup for the whole table, opened for whichever row's button was clicked.
const uploadModalPoCode = ref(null);
const uploadModalVendorCode = ref(null);
function openUploadModal(p) {
  uploadModalPoCode.value = p.po_code;
  uploadModalVendorCode.value = p.vendor_code;
}
</script>

<template>
  <SummaryKpis :tiles="kpiTiles" />

  <div class="pay-grid">
    <section v-for="c in cards" :key="c.id" class="table-card">
      <h3 class="card-caption">{{ c.title }}<span class="pay-card-sub">{{ c.sub }}</span></h3>
      <ul v-if="c.buckets.length" class="pay-bars">
        <li v-for="b in c.buckets" :key="b.key">
          <button
            type="button" class="pay-bar" :class="{ sel: c.selected === b.key }" :disabled="!b.count && c.selected !== b.key"
            :title="`${plural(b.count, 'PO')} · ${fmtMoney(b.amount)} PO value -- click to ${c.selected === b.key ? 'clear' : 'filter'}`"
            @click="pickBucket(c.id, b.key)"
          >
            <div class="pay-bar-head">
              <span class="pay-bar-label">{{ b.label }}</span>
              <span class="pay-bar-fig" :class="b.count ? b.cls : 'muted'">{{ b.count ? fmtMoneyCompact(b.amount) : "–" }}<small>{{ plural(b.count, "PO") }}</small></span>
            </div>
            <div class="meter-track"><div class="meter-fill" :style="{ width: barPct(c.buckets, b) + '%' }"></div></div>
          </button>
        </li>
      </ul>
      <div v-else class="pay-note">No open supply in view.</div>
    </section>
  </div>

  <div class="bucket-tabs po-stage-tabs">
    <button type="button" class="bucket-tab" :class="{ active: activeStage === 'all' }" @click="activeStage = 'all'">
      All <span class="bucket-count">{{ stageCounts.all }}</span>
    </button>
    <button
      v-for="s in STAGES" :key="s.key" type="button" class="bucket-tab" :class="{ active: activeStage === s.key }"
      :title="s.hint" @click="activeStage = s.key"
    >
      <span class="po-stage-dot" :class="`po-dot-${s.cls}`"></span>{{ s.label }} <span class="bucket-count">{{ stageCounts[s.key] }}</span>
    </button>
  </div>

  <div class="fin-toolbar">
    <div class="field fin-search">
      <label for="po-search">Search</label>
      <input id="po-search" v-model="filters.search" type="text" placeholder="Vendor, PO code, facility, invoice…">
    </div>
    <div class="field">
      <label for="po-vendor">Vendor</label>
      <select id="po-vendor" v-model="filters.vendor">
        <option value="">All vendors</option>
        <option v-for="v in vendorOptions" :key="v.code" :value="v.code">{{ v.label }}</option>
      </select>
    </div>
    <div class="field po-narrow">
      <label for="po-facility">Facility</label>
      <select id="po-facility" v-model="filters.facility">
        <option value="">All</option>
        <option v-for="f in facilityOptions" :key="f" :value="f">{{ f }}</option>
      </select>
    </div>
    <div class="field po-narrow">
      <label for="po-age">Age</label>
      <select id="po-age" v-model="ageFilter">
        <option value="">Any</option>
        <option v-for="b in AGE_BUCKETS" :key="b.key" :value="b.key">{{ b.label }}</option>
      </select>
    </div>
    <div class="field po-narrow">
      <label for="po-status">Uniware status</label>
      <select id="po-status" v-model="filters.status">
        <option value="">All</option>
        <option v-for="s in statusOptions" :key="s" :value="s">{{ s }}</option>
      </select>
    </div>
    <button v-if="hasLocalFilters" type="button" class="fin-btn" @click="clearFilters">Clear filters</button>
    <div class="fin-toolbar-end">
      <span class="fin-total">{{ plural(tableRows.length, "PO") }} · {{ fmtMoneyCompact(tableValue) }}</span>
      <button type="button" class="fin-btn" :disabled="!tableRows.length" @click="exportCsv">Download CSV</button>
    </div>
  </div>

  <div class="table-card"><div class="table-scroll">
    <table class="po-table">
      <thead>
        <tr>
          <th>Vendor</th>
          <th>PO</th>
          <th><button type="button" class="po-sort" @click="toggleSort('age')">Created / age{{ sortMark("age") }}</button></th>
          <th>Stage</th>
          <th><button type="button" class="po-sort" @click="toggleSort('fill')">Received{{ sortMark("fill") }}</button></th>
          <th class="num"><button type="button" class="po-sort" @click="toggleSort('value')">PO value{{ sortMark("value") }}</button></th>
          <th>GRN / invoice</th>
          <th class="col-tight">Invoice</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!tableRows.length">
          <td colspan="8" class="empty-state">
            No purchase orders {{ activeStage === "all" ? "match these filters" : `in ${STAGE_BY_KEY[activeStage].label}` }}.
            <button v-if="hasLocalFilters" type="button" class="link-btn-inline" @click="clearFilters">Clear filters</button>
          </td>
        </tr>
        <tr v-for="d in tableRows" :key="d.p.po_code" class="clickable-row" @click="onOpenPo(d.p.po_code)">
          <td class="po-vendor">{{ vendorLabel(d.p.vendor_code, d.p.vendor_name) }}</td>
          <td>
            <div class="mono">{{ d.p.po_code }}</div>
            <div class="fac-code">{{ d.p.facility }}</div>
          </td>
          <td>
            <div class="mono">{{ fmtDateOnly(d.p.created_at) }}</div>
            <div
              v-if="SUPPLY_OPEN.has(d.stage) && d.age != null" class="po-age"
              :class="{ 'po-age-warn': d.age > 30 && d.age <= 60, 'po-age-crit': d.age > 60 }"
            >{{ d.age }}d open</div>
          </td>
          <td>
            <span class="chip" :class="`chip-${STAGE_BY_KEY[d.stage].cls}`">{{ stageLabel(d.p, d.stage) }}</span>
            <div v-if="d.stage === 'with_finance'" class="substep-boxes po-substeps">
              <span
                v-for="(s, i) in financeSteps(d.p)" :key="i" class="substep-box" :class="s.ok ? 'substep-good' : 'substep-critical'"
                role="img" :aria-label="s.tip" :data-tip="s.tip"
              ></span>
            </div>
            <template v-else-if="d.stage === 'grn_pending'">
              <div
                class="po-grn-gap mono"
                :title="`Invoice ${grnGap(d.p).invoices.join(', ')} -- invoiced ${fmtNum(grnGap(d.p).invoiced)}, GRN'd ${fmtNum(grnGap(d.p).received)}`"
              >Inv {{ fmtNum(grnGap(d.p).invoiced) }} · GRN {{ fmtNum(grnGap(d.p).received) }}</div>
              <button v-if="canChangeStage" type="button" class="link-btn-inline po-stage-btn" @click.stop="stageDialogPo = d.p">Change stage</button>
            </template>
            <div v-else-if="d.stage === 'with_vendor'" class="po-request">
              <div v-for="r in openRequests(d.p)" :key="r.id" :title="r.note ? `Note to vendor: ${r.note}` : 'No note'">
                {{ r.requested_by_name || "Team" }} · {{ ago(r.requested_at) }}
                <button v-if="canChangeStage" type="button" class="link-btn-inline" @click.stop="withdraw(r)">Withdraw</button>
              </div>
            </div>
            <div v-else class="po-uniware"><StatusChip :status="d.p.status" /></div>
          </td>
          <td class="po-fill">
            <div class="po-fill-head mono">
              <span>{{ fmtNum(d.p.qty_received) }} / {{ fmtNum(d.p.qty_ordered) }}</span>
              <span class="po-fill-pct">{{ Math.round(d.fill) }}%</span>
            </div>
            <div class="meter-track"><div class="meter-fill" :class="{ 'po-fill-done': d.fill >= 100 }" :style="{ width: d.fill + '%' }"></div></div>
          </td>
          <td class="num mono">{{ fmtMoney(d.p.total_amount) }}</td>
          <td>
            <span v-if="!grnInfo(d.p.po_code).raised" class="cell-empty">not yet raised</span>
            <template v-else>
              <div class="invoice-list">{{ grnInfo(d.p.po_code).invoices.join(", ") || "–" }}</div>
              <div class="grn-amt">
                {{ fmtMoney(grnInfo(d.p.po_code).recv) }}
                <span v-if="grnInfo(d.p.po_code).rej" class="grn-rej">−{{ fmtMoney(grnInfo(d.p.po_code).rej) }} rej.</span>
              </div>
            </template>
          </td>
          <td class="col-tight" @click.stop>
            <div class="invoice-upload-cell">
              <InvoiceUploadButton :on-click="() => openUploadModal(d.p)" />
              <span
                v-if="uploadStatus(d.p.po_code).expected > 0"
                class="chip invoice-pending-chip" :class="uploadStatus(d.p.po_code).pending ? 'chip-critical' : 'chip-good'"
                :title="`${uploadStatus(d.p.po_code).uploaded} of ${uploadStatus(d.p.po_code).expected} invoice PDF(s) uploaded`"
              >{{ uploadStatus(d.p.po_code).uploaded }}/{{ uploadStatus(d.p.po_code).expected }}</span>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div></div>

  <p v-if="withdrawError" class="field-hint po-error">{{ withdrawError }}</p>

  <GrnStageDialog
    :model-value="!!stageDialogPo" :po="stageDialogPo" :short-uploads="stageDialogUploads"
    :vendor-name="stageDialogPo ? vendorLabel(stageDialogPo.vendor_code, stageDialogPo.vendor_name) : ''"
    :requester-label="uploaderLabel"
    @update:model-value="(v) => { if (!v) stageDialogPo = null }"
  />

  <InvoiceUploadModal
    :model-value="!!uploadModalPoCode"
    :po-code="uploadModalPoCode || ''"
    :vendor-code="uploadModalVendorCode || ''"
    :uploader-label="uploaderLabel"
    @update:model-value="(v) => { if (!v) uploadModalPoCode = null }"
  />
</template>
