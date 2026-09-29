// Spares section -- one shared store for all four views (Summary, Spares Inventory,
// Warehouse stock, Appendix), so an Appendix edit shows up everywhere instantly.
//
//   spares_sku_master      <- sheet "SKU list and uni data" (scripts/sync_spares.py)
//   spares_wh_inventory    <- Uniware good/bad, every SKU x 7 facilities (same script)
//   spares_drr             <- per-WH DRR from Jarvis 485614 (local sync_sla_portal.js)
//   spares_status_override <- Appendix edits (browser-written, per SKU x facility)
//   spares_vendor_override <- Appendix edits (browser-written, per SKU)
//
// Effective status: override if present; else sheet category Discontinued -> Obsolete;
// else in the sheet -> Ongoing; else (stocked, not in the sheet) -> NA. A row nobody
// has edited therefore keeps following the sheet; deleting an override resets it.
import { ref, computed, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

const POLL_INTERVAL_MS = 5 * 60 * 1000;
const PAGE = 1000; // PostgREST's default max-rows

export const STATUSES = ["Ongoing", "Obsolete", "NA"];

// Uniware facilities, Pataudi / Panchla kept separate (Warehouse stock, Appendix).
export const FACILITIES = [
  { code: "PB-UC-GGN", label: "Gurgaon" },
  { code: "PB-UC-GGN-PATAUDI", label: "Pataudi" },
  { code: "PB-UC-BLR", label: "Bangalore" },
  { code: "PB-UC-BOMBAY", label: "Mumbai" },
  { code: "PB-UC-KOL", label: "Kolkata" },
  { code: "PB-UC-KOL-PANCHLA", label: "Panchla" },
  { code: "PB-UC-HYD", label: "Hyderabad" },
];
export const FACILITY_LABEL = Object.fromEntries(FACILITIES.map((f) => [f.code, f.label]));

// The 5 planning warehouses (Spares Inventory, Summary): GGN and KOL are clubbed.
export const WAREHOUSES = [
  { key: "ggn", label: "GGN", codes: ["PB-UC-GGN", "PB-UC-GGN-PATAUDI"] },
  { key: "blr", label: "BLR", codes: ["PB-UC-BLR"] },
  { key: "bom", label: "BOM", codes: ["PB-UC-BOMBAY"] },
  { key: "kol", label: "KOL", codes: ["PB-UC-KOL", "PB-UC-KOL-PANCHLA"] },
  { key: "hyd", label: "HYD", codes: ["PB-UC-HYD"] },
];

// The sheet's "Spares excluding IK & Discontinued" table leaves these categories out.
const SUMMARY_EXCLUDED_CATEGORIES = new Set(["ik", "refresh", "dummy", "discontinued", ""]);

export const DOI_TARGET = 60;

// The sheet hand-set DRR 100 for these Refresh SKUs at GGN/BOM/HYD (their real DRR is
// ~0-0.4); kept on moving DRR to Jarvis 485614, at the user's request (2026-09-29).
const REFRESH_DRR_OVERRIDE = {
  skus: new Set([
    "UC/RO/N/PRECFIL/P/REFRESH",
    "UC/NATIVE/HealthBooster/P/REFRESH",
    "UC/NATIVE/PREFIL/ASSEMBLY/10/REFRESH",
    "UC/NATIVE/LIFEBOOSTER",
    "UC/NATIVE/SRT/REFRESH",
  ]),
  whs: new Set(["ggn", "bom", "hyd"]),
  drr: 100,
};

export const BUCKETS = [
  { key: "stockout", label: "Stock out" },
  { key: "0-7", label: "0-7" },
  { key: "8-15", label: "8-15" },
  { key: "16-30", label: "16-30" },
  { key: "31-60", label: "31-60" },
  { key: ">60", label: ">60" },
];

export function bucketOf(good, drr) {
  if (!(good > 0)) return "stockout";
  if (!(drr > 0)) return null; // stock but no demand -- the sheet leaves these unbucketed
  const doi = Math.floor(good / drr);
  if (doi <= 7) return "0-7";
  if (doi <= 15) return "8-15";
  if (doi <= 30) return "16-30";
  if (doi <= 60) return "31-60";
  return ">60";
}

function normVendor(v) {
  const s = (v || "").trim();
  return !s || /^n\/?a$/i.test(s) ? "NA" : s;
}

async function fetchAll(table, ...orderCols) {
  const out = [];
  for (let from = 0; ; from += PAGE) {
    let q = supabase.from(table).select("*");
    for (const c of orderCols) q = q.order(c);
    const { data, error } = await q.range(from, from + PAGE - 1);
    if (error) return { data: null, error };
    out.push(...data);
    if (data.length < PAGE) return { data: out, error: null };
  }
}

export function useSparesData(editorLabel) {
  const master = ref([]);
  const inventory = ref([]);
  const drrRows = ref([]);
  const statusOv = ref({}); // "sku|facility" -> row
  const vendorOv = ref({}); // sku -> row
  const loaded = ref(false);
  const loadError = ref("");
  const saveError = ref("");

  async function refresh() {
    const [m, inv, dr, so, vo] = await Promise.all([
      fetchAll("spares_sku_master", "sku"),
      fetchAll("spares_wh_inventory", "id"),
      fetchAll("spares_drr", "sku", "wh"),
      fetchAll("spares_status_override", "sku"),
      fetchAll("spares_vendor_override", "sku"),
    ]);
    if (!m.error) master.value = m.data;
    if (!inv.error) inventory.value = inv.data;
    if (!dr.error) drrRows.value = dr.data;
    if (!so.error) statusOv.value = Object.fromEntries(so.data.map((r) => [`${r.sku}|${r.facility}`, r]));
    if (!vo.error) vendorOv.value = Object.fromEntries(vo.data.map((r) => [r.sku, r]));
    loadError.value = m.error?.message || inv.error?.message || dr.error?.message || so.error?.message || vo.error?.message || "";
    loaded.value = true;
  }

  const masterBySku = computed(() => new Map(master.value.map((r) => [r.sku, r])));

  const stock = computed(() => {
    const map = new Map();
    for (const r of inventory.value) map.set(`${r.facility}|${r.sku}`, { good: +r.good_qty || 0, bad: +r.bad_qty || 0 });
    return map;
  });
  const drrMap = computed(() => new Map(drrRows.value.map((r) => [`${r.sku}|${r.wh}`, +r.drr || 0])));
  const drrOf = (sku, wh) => {
    if (REFRESH_DRR_OVERRIDE.skus.has(sku) && REFRESH_DRR_OVERRIDE.whs.has(wh.key)) return REFRESH_DRR_OVERRIDE.drr;
    return drrMap.value.get(`${sku}|${wh.key}`) || 0;
  };

  const stockAt = (sku, facility) => stock.value.get(`${facility}|${sku}`) || { good: 0, bad: 0 };

  // Sheet SKUs in sheet order, then any other SKU stocked at a warehouse.
  const allSkus = computed(() => {
    const sheet = [...master.value].sort((a, b) => (a.sheet_order ?? 0) - (b.sheet_order ?? 0)).map((r) => r.sku);
    const seen = new Set(sheet);
    const extra = [...new Set(inventory.value.map((r) => r.sku))].filter((s) => !seen.has(s)).sort();
    return [...sheet, ...extra];
  });

  function autoStatus(sku) {
    const m = masterBySku.value.get(sku);
    if (!m) return "NA";
    return (m.category || "").trim().toLowerCase() === "discontinued" ? "Obsolete" : "Ongoing";
  }
  const statusOf = (sku, facility) => statusOv.value[`${sku}|${facility}`]?.status || autoStatus(sku);
  const isEdited = (sku, facility) => !!statusOv.value[`${sku}|${facility}`];

  const sheetVendor = (sku) => normVendor(masterBySku.value.get(sku)?.sheet_vendor);
  const vendorOf = (sku) => (vendorOv.value[sku] ? normVendor(vendorOv.value[sku].vendor) : sheetVendor(sku));
  const vendorEdited = (sku) => !!vendorOv.value[sku];
  const vendorOptions = computed(() => {
    const s = new Set();
    for (const r of master.value) if (normVendor(r.sheet_vendor) !== "NA") s.add(normVendor(r.sheet_vendor));
    for (const r of Object.values(vendorOv.value)) if (normVendor(r.vendor) !== "NA") s.add(normVendor(r.vendor));
    return [...s].sort((a, b) => a.localeCompare(b));
  });
  const categoryOf = (sku) => (masterBySku.value.get(sku)?.category || "").trim();

  // ---- clubbed planning-warehouse figures -----------------------------------------------
  const whOngoing = (sku, wh) => wh.codes.some((c) => statusOf(sku, c) === "Ongoing");
  // Only the Ongoing member facility's stock counts toward a clubbed warehouse.
  const whGood = (sku, wh) => wh.codes.reduce((t, c) => t + (statusOf(sku, c) === "Ongoing" ? stockAt(sku, c).good : 0), 0);

  function whFigures(sku, wh) {
    const m = masterBySku.value.get(sku);
    const drr = drrOf(sku, wh);
    const good = whGood(sku, wh);
    const inTransit = +(m?.[`in_transit_${wh.key}`] || 0);
    const delivery = m?.[`delivery_${wh.key}`] || null;
    const doi = drr > 0 ? good / drr : null;
    const required = Math.max(0, Math.ceil(DOI_TARGET * drr - good - inTransit));
    return { drr, good, inTransit, delivery, doi, required, bucket: bucketOf(good, drr) };
  }

  function supplyStatus(sku, wh) {
    const m = masterBySku.value.get(sku);
    const d = (m?.[`delivery_${wh.key}`] || "").trim();
    const it = +(m?.[`in_transit_${wh.key}`] || 0);
    if (/grn\s*pending/i.test(d)) return { kind: "grn", text: "GRN Pending" };
    if (it > 0) return { kind: "transit", text: `In transit · ${it.toLocaleString("en-IN")}${d ? ` · ETA ${d}` : ""}` };
    if (m?.next_dispatch_date) {
      const q = m.next_dispatch_qty ? ` · ${(+m.next_dispatch_qty).toLocaleString("en-IN")}` : "";
      return { kind: "planned", text: `Dispatch planned ${m.next_dispatch_date}${q}` };
    }
    return { kind: "none", text: "–" };
  }

  const inSummaryScope = (sku) =>
    masterBySku.value.has(sku) && !SUMMARY_EXCLUDED_CATEGORIES.has(categoryOf(sku).toLowerCase());

  // ---- writes -----------------------------------------------------------------------------
  const who = () => (typeof editorLabel === "function" ? editorLabel() : editorLabel?.value ?? editorLabel) || null;

  async function setStatus(sku, facility, status) {
    const key = `${sku}|${facility}`;
    const prev = statusOv.value[key];
    const row = { sku, facility, status, updated_by: who(), updated_at: new Date().toISOString() };
    statusOv.value = { ...statusOv.value, [key]: row };
    const { error } = await supabase.from("spares_status_override").upsert(row, { onConflict: "sku,facility" });
    if (error) {
      const next = { ...statusOv.value };
      if (prev) next[key] = prev; else delete next[key];
      statusOv.value = next;
      saveError.value = `Couldn't save status for ${sku}: ${error.message}`;
    } else saveError.value = "";
  }

  async function resetStatus(sku, facility) {
    const key = `${sku}|${facility}`;
    const prev = statusOv.value[key];
    if (!prev) return;
    const next = { ...statusOv.value };
    delete next[key];
    statusOv.value = next;
    const { error } = await supabase.from("spares_status_override").delete().eq("sku", sku).eq("facility", facility);
    if (error) {
      statusOv.value = { ...statusOv.value, [key]: prev };
      saveError.value = `Couldn't reset ${sku}: ${error.message}`;
    } else saveError.value = "";
  }

  async function setVendor(sku, vendor) {
    const v = (vendor || "").trim();
    if (!v) return resetVendor(sku);
    const prev = vendorOv.value[sku];
    const row = { sku, vendor: v, updated_by: who(), updated_at: new Date().toISOString() };
    vendorOv.value = { ...vendorOv.value, [sku]: row };
    const { error } = await supabase.from("spares_vendor_override").upsert(row, { onConflict: "sku" });
    if (error) {
      const next = { ...vendorOv.value };
      if (prev) next[sku] = prev; else delete next[sku];
      vendorOv.value = next;
      saveError.value = `Couldn't save vendor for ${sku}: ${error.message}`;
    } else saveError.value = "";
  }

  async function resetVendor(sku) {
    const prev = vendorOv.value[sku];
    if (!prev) return;
    const next = { ...vendorOv.value };
    delete next[sku];
    vendorOv.value = next;
    const { error } = await supabase.from("spares_vendor_override").delete().eq("sku", sku);
    if (error) {
      vendorOv.value = { ...vendorOv.value, [sku]: prev };
      saveError.value = `Couldn't reset vendor for ${sku}: ${error.message}`;
    } else saveError.value = "";
  }

  const sheetSyncedAt = computed(() => master.value.reduce((t, r) => (r.synced_at > t ? r.synced_at : t), "") || null);
  const drrSyncedAt = computed(() => drrRows.value.reduce((t, r) => (r.synced_at > t ? r.synced_at : t), "") || null);
  const stockSyncedAt = computed(() => inventory.value.reduce((t, r) => (r.synced_at > t ? r.synced_at : t), "") || null);

  let intervalId = null;
  onMounted(async () => {
    await refresh();
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => intervalId && clearInterval(intervalId));

  return {
    loaded, loadError, saveError, refresh, master, masterBySku, allSkus, stockAt,
    statusOf, isEdited, setStatus, resetStatus,
    vendorOf, sheetVendor, vendorEdited, vendorOptions, setVendor, resetVendor, categoryOf,
    whOngoing, whFigures, supplyStatus, inSummaryScope, sheetSyncedAt, stockSyncedAt, drrSyncedAt,
  };
}
