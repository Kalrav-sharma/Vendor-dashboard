// Logistics Health Card › Inventory Risk Monitor. Scores every facility × SKU pair into
// exactly ONE band: Stockout (on-hand ≤ 0), <7 DOI, 7–<15 DOI, or healthy. The bands are
// mutually exclusive, as the user decided on 2026-09-25, so a row's cells add up to the
// total at risk.
//
// Built on the S&OP tables through their existing composables. No new sync is needed.
//   Warehouses  sop_facility_drr_doi WAREHOUSE (5 cities × 6 SKUs)
//   Dark Stores sop_dark_store_inventory (19 stores × 6 SKUs), DOI from sop_facility_drr_doi
//   MFCs        sop_dark_store_inventory city 'SFX MFCs' (6 × 6). Stockout only: no sales
//               history yet, so their DOI is undefined
//   MT          sop_inventory_channel + sop_channel_drr_doi channel 'MT', 4 SKUs (not M0/M3)
// Null DOI with stock on hand means no recent sales. That is not a risk, and such a pair
// is counted as "no DOI" rather than as healthy.
import { computed } from "vue";
import { useSopUcAppData } from "./useSopUcAppData.js";
import { useSopInventoryData } from "./useSopInventoryData.js";

export const SKUS = ["M0", "M1-2nd Gen", "M1 Pro", "M2 Pro", "M3", "M3 Pro"];
const MT_SKUS = ["M1-2nd Gen", "M1 Pro", "M2 Pro", "M3 Pro"];
// Not dark stores any more (2026-09-25). Also removed from the S&OP roster in sop_common.py.
// This guard covers the window before the next S&OP sync clears their rows.
const DROPPED_STORES = new Set(["PB-UC-DEL-JHILMIL", "PB-UC-GGN-SOHNA"]);
const MFC_BUCKET = "SFX MFCs";

export const BANDS = [
  { key: "stockout", label: "Stockout", sub: "0 on hand" },
  { key: "lt7", label: "< 7 DOI", sub: "under a week" },
  { key: "lt15", label: "< 15 DOI", sub: "7 to 15 days" },
];

function band(onHand, doi, doiKnown) {
  if (onHand == null) return "unknown";
  if (onHand <= 0) return "stockout";
  if (!doiKnown || doi == null) return "nodoi";
  if (doi < 7) return "lt7";
  if (doi < 15) return "lt15";
  return "ok";
}

const prettyStore = code => code.replace(/^PB-UC-/, "").replace(/^SFX-/, "SFX ").replace(/-/g, " ")
  .toLowerCase().replace(/\b\w/g, c => c.toUpperCase()).replace(/^(Blr|Del|Kol|Bom|Hyd|Ggn|Sfx) /, (m, p) => `${p.toUpperCase()} · `);

export function useHealthInventoryRisk() {
  const { darkStoreRows, facilityDrrDoiRows, stockSyncedAt, loadError: e1 } = useSopUcAppData();
  const { channelRows, channelDrrDoiRows, loadError: e2 } = useSopInventoryData();

  const groups = computed(() => {
    const doiBy = new Map(facilityDrrDoiRows.value.map(r => [`${r.facility}|${r.sku}`, r]));
    const pairs = { warehouses: [], darkStores: [], mfcs: [], mt: [] };

    facilityDrrDoiRows.value.filter(r => r.facility_type === "WAREHOUSE").forEach(r => {
      pairs.warehouses.push({ where: r.facility, sku: r.sku, onHand: r.on_hand, drr: r.drr, doi: r.doi, band: band(r.on_hand, r.doi, true) });
    });
    darkStoreRows.value.filter(r => !DROPPED_STORES.has(r.store)).forEach(r => {
      const isMfc = r.city === MFC_BUCKET || r.store.startsWith("PB-UC-SFX-");
      const d = doiBy.get(`${r.store}|${r.sku}`);
      const p = { where: prettyStore(r.store), code: r.store, sku: r.sku, onHand: r.on_hand, drr: d?.drr ?? null, doi: d?.doi ?? null };
      (isMfc ? pairs.mfcs : pairs.darkStores).push({ ...p, band: band(r.on_hand, p.doi, !isMfc && !!d) });
    });
    const mtDoi = new Map(channelDrrDoiRows.value.filter(r => r.channel === "MT").map(r => [r.sku, r]));
    channelRows.value.filter(r => r.channel === "MT" && MT_SKUS.includes(r.sku)).forEach(r => {
      const d = mtDoi.get(r.sku);
      // doi_flag '>60' = stock outlasts the forecast horizon: healthy, not "no DOI"
      const doi = d?.doi_flag === ">60" ? 999 : d?.doi ?? null;
      pairs.mt.push({ where: "Modern Trade (Croma + Vijay Sales)", sku: r.sku, onHand: r.qty, drr: d?.drr ?? null, doi: d?.doi_flag === ">60" ? null : d?.doi ?? null, doiFlag: d?.doi_flag, band: band(r.qty, doi, !!d) });
    });

    const ROWS = [
      { key: "warehouses", label: "Warehouses", sub: "5 mother warehouses", doiNa: false },
      { key: "darkStores", label: "Dark Stores", sub: "19 dark stores", doiNa: false },
      { key: "mfcs", label: "MFCs", sub: "6 SFX micro-fulfilment centres", doiNa: true },
      { key: "mt", label: "MT", sub: "Modern trade · excl. M0 & M3", doiNa: false },
    ];
    return ROWS.map(g => {
      const list = pairs[g.key];
      const count = k => list.filter(p => p.band === k).length;
      return {
        ...g,
        monitored: list.length,
        stockout: count("stockout"), lt7: count("lt7"), lt15: count("lt15"),
        noDoi: count("nodoi"),
        atRisk: count("stockout") + count("lt7") + count("lt15"),
        pairs: list,
      };
    });
  });

  const totals = computed(() => {
    const g = groups.value;
    return {
      stockout: g.reduce((s, x) => s + x.stockout, 0),
      lt7: g.reduce((s, x) => s + x.lt7, 0),
      lt15: g.reduce((s, x) => s + x.lt15, 0),
      monitored: g.reduce((s, x) => s + x.monitored, 0),
      atRisk: g.reduce((s, x) => s + x.atRisk, 0),
    };
  });
  const loadError = computed(() => e1.value || e2.value);

  return { groups, totals, stockSyncedAt, loadError };
}
