<script setup>
// Finance › Action Required -- Finance's landing page and daily worklist. Four queues
// (useFinanceActions.js): ready to book, booked awaiting payout, needs attention, waiting on
// others. Each tab filters by vendor/search and downloads as CSV for booking in Oracle.
// "New" marks invoices that became ready since this browser last opened the page.
import { ref, computed, watch } from "vue";
import { fmtMoney, fmtMoneyCompact, fmtDateOnly } from "../../format.js";
import { paymentLedger, sumAmount } from "../../composables/usePaymentSummary.js";
import { financeQueues, daysSince, lastPayoutSync } from "../../composables/useFinanceActions.js";
import { useInvoiceUploads } from "../../composables/useInvoiceUploads.js";
import { downloadCsv } from "../sla/slaUtil.js";
import SummaryKpis from "../SummaryKpis.vue";
import ViewInvoiceButton from "../ViewInvoiceButton.vue";
import downloadIcon from "../../assets/icons/download.png";
import "./payments.css";

const props = defineProps({
  uploads: { type: Array, required: true }, // allUploads (po_invoice_uploads)
  posWithPayment: { type: Array, default: () => [] }, // booked/paid POs with no portal invoice
  pos: { type: Array, default: () => [] }, // every PO -- only read for the payout sync stamp
  vendorLabel: { type: Function, required: true },
  onOpenPo: { type: Function, required: true },
  onOpenVendor: { type: Function, required: true },
  onRefresh: { type: Function, required: true }, // async () => void -- re-fetches uploads
  active: { type: Boolean, default: true }, // this page is the visible tab
  bookerLabel: { type: String, default: "" }, // current user's display name, recorded on a "Mark as booked"
});

const { viewCreditNote, markInvoiceBooked, unmarkInvoiceBooked, workingIds } = useInvoiceUploads();

const queues = computed(() => financeQueues(paymentLedger(props.uploads, props.posWithPayment)));

// Ready tab's "Mark as booked" / Booked tab's "Undo" -- a plain DB update (finance_booked_*,
// see schema.sql), not the payout-sync columns, so it's immediate and doesn't fight the next
// weekly sync. workingIds (from useInvoiceUploads, keyed "book:<id>") disables the button
// mid-request; errors use the same alert() pattern useInvoiceUploads already uses elsewhere.
function bookingKey(e) { return e.row ? `book:${e.row.id}` : null; }
function isBooking(e) { const k = bookingKey(e); return !!k && workingIds.has(k); }
async function handleMarkBooked(e) {
  if (!e.row) return;
  const result = await markInvoiceBooked(e.row, props.bookerLabel);
  if (!result.ok) alert(`Couldn't mark as booked: ${result.error}`);
}
async function handleUndoBooked(e) {
  if (!e.row) return;
  const result = await unmarkInvoiceBooked(e.row);
  if (!result.ok) alert(`Couldn't undo: ${result.error}`);
}


// --- "New since your last visit" (this browser only) -------------------------------------
// Stamped the first time the page is actually shown this load, not when AdminApp mounts it
// behind another tab.
const VISIT_KEY = "fin-action-last-visit";
const lastVisit = ref(null); // ms
let stamped = false;
watch(() => props.active, (a) => {
  if (!a || stamped) return;
  stamped = true;
  try {
    const prev = localStorage.getItem(VISIT_KEY);
    lastVisit.value = prev ? new Date(prev).getTime() : null;
    localStorage.setItem(VISIT_KEY, new Date().toISOString());
  } catch { /* storage blocked -- nothing is marked new */ }
}, { immediate: true });
const isNew = (e) => !!(lastVisit.value && e.readySince && new Date(e.readySince).getTime() > lastVisit.value);

// --- Payout sync freshness + refresh ------------------------------------------------------
const syncedAt = computed(() => lastPayoutSync(props.uploads, props.pos));
const syncAge = computed(() => daysSince(syncedAt.value));
const refreshedAt = ref(new Date());
const refreshing = ref(false);
async function refresh() {
  refreshing.value = true;
  try { await props.onRefresh(); refreshedAt.value = new Date(); } finally { refreshing.value = false; }
}
const timeTxt = (d) => d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });

// --- KPIs ----------------------------------------------------------------------------------
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const kpis = computed(() => {
  const q = queues.value;
  const oldest = q.ready.reduce((m, e) => Math.max(m, daysSince(e.readySince) ?? 0), 0);
  const fresh = q.ready.filter(isNew).length;
  const bookedSoon = q.booked.filter((e) => e.days != null && e.days <= 0 && e.days >= -7);
  const bookedOverdue = q.booked.filter((e) => e.days != null && e.days > 0);
  return [
    {
      label: "Ready to book", value: fmtMoneyCompact(sumAmount(q.ready)), cls: q.ready.length ? "info" : "",
      sublabel: q.ready.length ? `${plural(q.ready.length, "invoice")} · oldest ${oldest}d${fresh ? ` · ${fresh} new` : ""}` : "Nothing waiting",
    },
    { label: "Booked · due in 7 days", value: fmtMoneyCompact(sumAmount(bookedSoon)), sublabel: plural(bookedSoon.length, "invoice") },
    {
      label: "Booked · overdue", value: fmtMoneyCompact(sumAmount(bookedOverdue)), cls: bookedOverdue.length ? "critical" : "",
      sublabel: bookedOverdue.length ? `${plural(bookedOverdue.length, "invoice")} past due, not paid` : "Nothing past due",
    },
    {
      label: "Needs attention", value: plural(q.attention.length, "invoice"), cls: q.attention.length ? "critical" : "",
      sublabel: "Check failed, unread, or duplicate",
    },
    {
      label: "Waiting on others", value: fmtMoneyCompact(sumAmount(q.waiting)),
      sublabel: `${q.waiting.filter((e) => e.recon === "mismatch").length} on vendor CN · ${q.waiting.filter((e) => e.recon === "needs_review").length} on GRN`,
    },
  ];
});

// --- Tabs + filters ------------------------------------------------------------------------
const TABS = [
  { key: "ready", label: "Ready to book" },
  { key: "booked", label: "Booked · awaiting payout" },
  { key: "attention", label: "Needs attention" },
  { key: "waiting", label: "Waiting on others" },
];
const tab = ref("ready");
const vendor = ref("");
const search = ref("");
const basis = ref(""); // ready tab only: "" | "reconciled" | "cn"

const vendorChoices = computed(() => {
  const codes = new Set(Object.values(queues.value).flat().map((e) => e.vendorCode));
  return [...codes].map((c) => ({ code: c, label: props.vendorLabel(c) })).sort((a, b) => a.label.localeCompare(b.label));
});
watch(vendorChoices, (list) => { if (vendor.value && !list.some((v) => v.code === vendor.value)) vendor.value = ""; });

const byDue = (a, b) => (b.days ?? -9999) - (a.days ?? -9999);
const byTime = (k) => (a, b) => String(a[k] || "").localeCompare(String(b[k] || ""));
const SORTS = {
  ready: (a, b) => byDue(a, b) || byTime("readySince")(a, b),
  booked: byDue,
  attention: byTime("receivedAt"),
  waiting: byTime("waitingSince"),
};

const rows = computed(() => {
  const q = search.value.trim().toLowerCase();
  return queues.value[tab.value]
    .filter((e) => !vendor.value || e.vendorCode === vendor.value)
    .filter((e) => tab.value !== "ready" || !basis.value || e.basis === basis.value)
    .filter((e) => !q || [props.vendorLabel(e.vendorCode), e.po, e.inv].some((s) => String(s).toLowerCase().includes(q)))
    .sort(SORTS[tab.value]);
});
const rowsAmount = computed(() => sumAmount(rows.value));

// --- Cell helpers --------------------------------------------------------------------------
function dueChip(e) {
  if (e.days == null) return { text: "–", cls: "" };
  if (e.days > 0) return { text: `${e.days}d overdue`, cls: "chip chip-critical" };
  if (e.days === 0) return { text: "Due today", cls: "chip chip-open" };
  if (e.days >= -7) return { text: `in ${-e.days}d`, cls: "chip chip-open" };
  return { text: `in ${-e.days}d`, cls: "" };
}
const agoTxt = (iso) => { const d = daysSince(iso); return d == null ? "–" : d === 0 ? "today" : `${d}d`; };

// --- CSV -----------------------------------------------------------------------------------
const CSV = {
  ready: {
    headers: ["Vendor", "PO code", "Invoice number", "Basis", "Invoice value", "GRN value", "Due date", "Due date estimated", "Days overdue", "Ready since"],
    row: (e) => [props.vendorLabel(e.vendorCode), e.po, e.inv, e.basis === "cn" ? "Credit note uploaded" : "Reconciled",
      e.amount, e.grnAmount, e.due, e.estimated ? "yes" : "", e.days != null && e.days > 0 ? e.days : "", (e.readySince || "").slice(0, 10)],
  },
  booked: {
    headers: ["Vendor", "PO code", "Invoice number", "Amount", "Due date", "Days overdue", "Booked via", "Booked as of", "Booked by"],
    row: (e) => [props.vendorLabel(e.vendorCode), e.po, e.inv, e.amount, e.due, e.days != null && e.days > 0 ? e.days : "",
      e.source === "sync" ? "Payout sync" : "Portal (awaiting sync)",
      ((e.source === "sync" ? e.syncedAt : e.bookedAt) || "").slice(0, 10), e.bookedBy || ""],
  },
  attention: {
    headers: ["Vendor", "PO code", "Invoice number", "Amount", "Issues", "Uploaded"],
    row: (e) => [props.vendorLabel(e.vendorCode), e.po, e.inv, e.amount, e.issues.join("; "), (e.receivedAt || "").slice(0, 10)],
  },
  waiting: {
    headers: ["Vendor", "PO code", "Invoice number", "Amount", "Waiting on", "Reason", "Waiting since", "Due date"],
    row: (e) => [props.vendorLabel(e.vendorCode), e.po, e.inv, e.amount, e.waitingOn, e.reason, (e.waitingSince || "").slice(0, 10), e.due],
  },
};
function exportCsv() {
  const spec = CSV[tab.value];
  downloadCsv(`finance_${tab.value}_${new Date().toISOString().slice(0, 10)}.csv`, spec.headers, rows.value.map(spec.row));
}
</script>

<template>
  <div class="fin-status" :class="{ stale: syncAge == null || syncAge > 7 }">
    <span>
      <b>Payout file last synced:</b>
      <template v-if="syncedAt">{{ fmtDateOnly(syncedAt) }} ({{ syncAge === 0 ? "today" : `${syncAge}d ago` }})</template>
      <template v-else>never</template>
      <span class="fin-status-hint"> -- invoices booked in Oracle after this still show as Ready to book until the next sync.</span>
    </span>
    <span class="fin-status-right">
      Refreshed {{ timeTxt(refreshedAt) }}
      <button type="button" class="fin-btn" :disabled="refreshing" @click="refresh">{{ refreshing ? "Refreshing…" : "Refresh" }}</button>
    </span>
  </div>

  <SummaryKpis :tiles="kpis" />

  <div class="bucket-tabs">
    <button
      v-for="t in TABS" :key="t.key" type="button"
      class="bucket-tab" :class="{ active: tab === t.key }" @click="tab = t.key"
    >{{ t.label }} <span class="bucket-count">{{ queues[t.key].length }}</span></button>
  </div>

  <div class="fin-toolbar">
    <div class="field">
      <label for="fin-vendor">Vendor</label>
      <select id="fin-vendor" v-model="vendor">
        <option value="">All vendors</option>
        <option v-for="v in vendorChoices" :key="v.code" :value="v.code">{{ v.label }}</option>
      </select>
    </div>
    <div v-if="tab === 'ready'" class="field">
      <label for="fin-basis">Basis</label>
      <select id="fin-basis" v-model="basis">
        <option value="">Reconciled + credit note</option>
        <option value="reconciled">Reconciled</option>
        <option value="cn">Credit note uploaded</option>
      </select>
    </div>
    <div class="field fin-search">
      <label for="fin-search">Search</label>
      <input id="fin-search" v-model="search" type="text" placeholder="Vendor, PO code, invoice number…">
    </div>
    <div class="fin-toolbar-end">
      <span class="fin-total">{{ plural(rows.length, "invoice") }} · {{ fmtMoney(rowsAmount) }}</span>
      <button type="button" class="fin-btn" :disabled="!rows.length" @click="exportCsv">Download CSV</button>
    </div>
  </div>

  <div class="table-card"><div class="table-scroll">
    <table>
      <thead>
        <tr v-if="tab === 'ready'">
          <th>Vendor</th><th>PO code</th><th>Invoice number</th><th>Basis</th>
          <th class="num">Invoice value</th><th class="num">GRN value</th><th>Due date</th><th>Due</th>
          <th class="num" title="Days since it became ready to book (reconciled, or credit note uploaded)">Waiting</th><th>Invoice</th>
        </tr>
        <tr v-else-if="tab === 'booked'">
          <th>Vendor</th><th>PO code</th><th>Invoice number</th><th class="num">Amount</th>
          <th>Due date</th><th>Due</th><th title="Payout file sync, or Finance's own Mark as booked while the sync catches up">Booked</th><th>Invoice</th>
        </tr>
        <tr v-else-if="tab === 'attention'">
          <th>Vendor</th><th>PO code</th><th>Invoice number</th><th class="num">Amount</th>
          <th>Issue</th><th class="num">Uploaded</th><th>Invoice</th>
        </tr>
        <tr v-else>
          <th>Vendor</th><th>PO code</th><th>Invoice number</th><th class="num">Amount</th>
          <th>Waiting on</th><th>Reason</th><th class="num">Waiting</th><th>Due</th><th>Invoice</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!rows.length">
          <td colspan="10" class="empty-state">
            {{ queues[tab].length ? "Nothing matches these filters." : tab === "ready" ? "Nothing ready to book -- all caught up." : "Nothing here." }}
          </td>
        </tr>
        <tr v-for="e in rows" :key="e.key">
          <td><button type="button" class="link-btn-inline" title="Open this vendor's payment view" @click="onOpenVendor(e.vendorCode)">{{ vendorLabel(e.vendorCode) }}</button></td>
          <td class="mono"><button type="button" class="link-btn-inline" @click="onOpenPo(e.po)">{{ e.po }}</button></td>
          <td class="mono">
            {{ e.inv }}
            <span v-if="tab === 'ready' && isNew(e)" class="chip chip-info fin-new">New</span>
          </td>

          <template v-if="tab === 'ready'">
            <td><span class="chip" :class="e.basis === 'cn' ? 'chip-open' : 'chip-good'">{{ e.basis === "cn" ? "Credit note uploaded" : "Reconciled" }}</span></td>
            <td class="num mono">{{ fmtMoney(e.amount) }}</td>
            <td class="num mono" :class="{ critical: e.grnAmount != null && e.amount != null && Math.round(e.grnAmount) !== Math.round(e.amount) }">{{ fmtMoney(e.grnAmount) }}</td>
            <td class="mono">{{ fmtDateOnly(e.due) }}<span v-if="e.estimated" class="due-date-estimated-mark" title="Not printed on the invoice -- estimated as 45 days from the invoice date.">*</span></td>
            <td><span :class="dueChip(e).cls">{{ dueChip(e).text }}</span></td>
            <td class="num mono">{{ agoTxt(e.readySince) }}</td>
          </template>

          <template v-else-if="tab === 'booked'">
            <td class="num mono">{{ fmtMoney(e.amount) }}</td>
            <td class="mono">{{ fmtDateOnly(e.due) }}<span v-if="e.estimated" class="due-date-estimated-mark" title="Not printed on the invoice -- estimated as 45 days from the invoice date.">*</span></td>
            <td><span :class="dueChip(e).cls">{{ dueChip(e).text }}</span></td>
            <td>
              <template v-if="e.source === 'sync'">
                <span class="chip chip-good" title="Confirmed by the payout file sync">Synced</span>
                <span class="fin-booked-sub">{{ fmtDateOnly(e.syncedAt) }}</span>
              </template>
              <template v-else>
                <span class="chip chip-open" :title="`Marked booked by ${e.bookedBy || 'someone'} -- the payout sync hasn't confirmed it yet`">Marked in portal</span>
                <span class="fin-booked-sub">{{ agoTxt(e.bookedAt) }} by {{ e.bookedBy || "–" }}</span>
                <button type="button" class="link-btn-inline fin-undo" :disabled="isBooking(e)" @click="handleUndoBooked(e)">{{ isBooking(e) ? "…" : "Undo" }}</button>
              </template>
            </td>
          </template>

          <template v-else-if="tab === 'attention'">
            <td class="num mono">{{ fmtMoney(e.amount) }}</td>
            <td><span v-for="i in e.issues" :key="i" class="chip chip-critical fin-issue">{{ i }}</span></td>
            <td class="num mono">{{ agoTxt(e.receivedAt) }}</td>
          </template>

          <template v-else>
            <td class="num mono">{{ fmtMoney(e.amount) }}</td>
            <td>{{ e.waitingOn }}</td>
            <td><span class="chip" :class="e.recon === 'needs_review' ? 'chip-open' : 'chip-critical'">{{ e.reason }}</span></td>
            <td class="num mono">{{ agoTxt(e.waitingSince) }}</td>
            <td><span :class="dueChip(e).cls">{{ dueChip(e).text }}</span></td>
          </template>

          <td class="fin-docs">
            <template v-if="e.row">
              <ViewInvoiceButton :row="e.row" />
              <button v-if="e.row.credit_note_storage_path" class="icon-btn fin-cn" title="View credit note" aria-label="View credit note" @click.stop="viewCreditNote(e.row)">
                <img :src="downloadIcon" alt="" class="icon-mono"><span>CN</span>
              </button>
            </template>
            <span v-else class="cell-empty">No portal invoice</span>
            <button
              v-if="tab === 'ready'" type="button" class="fin-btn fin-book-btn"
              :disabled="isBooking(e)" @click="handleMarkBooked(e)"
            >{{ isBooking(e) ? "Booking…" : "Mark as booked" }}</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div></div>
  <p class="field-hint">
    <template v-if="tab === 'ready'">Reconciled = PO, GRN and invoice match. Credit note uploaded = the mismatch has a vendor credit note against it -- check it before booking. A GRN value in red differs from the invoice value.</template>
    <template v-else-if="tab === 'booked'">Booked in Oracle per the payout file, or marked booked by Finance in the portal while that sync catches up -- either way, payout not made yet. A portal mark can be undone if it was a mis-click; it clears itself once the sync confirms the real status, nothing to do there.</template>
    <template v-else-if="tab === 'attention'">The portal can't move these on its own. The payout sync matches on invoice number, so an invoice with none read will never pick up a booked/paid status.</template>
    <template v-else>Not bookable yet: the vendor owes a credit note, or the warehouse hasn't raised the GRN. Use this list to chase.</template>
  </p>
</template>
