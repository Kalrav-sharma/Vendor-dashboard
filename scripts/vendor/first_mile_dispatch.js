#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────────────────────
// VENDORED COPY — do not edit in isolation.
//
// Source: ~/.claude/scripts/parse_first_mile_dispatch_plan.js (the /first-mile-dispatch-decision
// skill). This is the same engine, so the portal's Daily Dispatch Planner tab and the skill can
// never disagree about the plan. A Python re-implementation was considered and rejected: 2,000
// lines of tier scoring, DOI laddering and scarce/surplus packing would drift the moment either
// side was touched.
//
// If the skill changes, RE-VENDOR THIS FILE and re-apply the four deltas below. They are the only
// intentional differences:
//   1. No Google Sheets write. The skill overwrites the live 'First Mile Plan' tab on every run;
//      the portal must never do that (cron + a live sheet someone is editing = lost work), so the
//      write block and its google-sheets.js dependency are deleted outright rather than flagged off.
//   2. Reads rows from the Sheets API (see --rows) instead of .xlsx files, because the Drive MCP
//      connector the skill uses doesn't exist on a CI runner.
//   3. Emits --json for the sync script. The terminal report still prints; JSON goes to a file.
//   4. PO_CUTOFF derives from the window instead of being a hardcoded date a cron would sail past.
//   5. Captures per-PO identity (number, SO number, channel) so the portal can list the individual
//      orders a plan leaves PARTIAL or needing a RESCHEDULE. The skill only reports shortfalls per
//      (date, warehouse, SKU); this adds a parallel structure and changes no arithmetic.
//
// Usage: node first_mile_dispatch.js --rows <payload.json> --json <out.json> [--no-today-production]
// ─────────────────────────────────────────────────────────────────────────────────────────
//
// FIRST-MILE DISPATCH DECISION — decides, for each day in the window, how much of each SKU
// to truck from Ronch (Pune) / Amber (Gurgaon) to which of the 5 UC App - RO warehouses.
//
// Method (greedy day-by-day simulation, not an LP solver — consistent with the sheet-only
// approximation style used by the sibling skills in this repo):
//   1. Opening balance per warehouse x SKU = current on-hand (Dispatch Planning "Inventory View").
//   2. Already-dispatched, in-transit stock is backtracked to an arrival day using the same
//      technique as parse_warehouse_stock_projection.js: match Dispatch Planning's aggregate
//      in-transit total against Raw Data Sheet's AMBER/RONCH-origin dispatch records (movement
//      type FM), most-recent-first, arrival day = dispatch day + TAT.
//   3. Daily depletion per warehouse x SKU = trailing-15-day DRR (Diwali Sales Plan - Overall tab,
//      warehouse row) + trailing-15-day DRR of every dark store mapped to that warehouse (summed
//      in, not separately balance-tracked — the warehouse is what ships to replenish the dark
//      store, so DS demand is folded into the warehouse's own effective outflow).
//   4. On top of that continuous DRR, discrete channel PO demand (Raw Data Sheet, movement type
//      MM, origin = warehouse code, channel in {Amazon, Flipkart, Croma, Vijay Sales, Reliance} —
//      confirmed whitelist; Primarc/Rentomojo/DTDC-Raftaar/"NA" rows excluded per user direction)
//      draws down the warehouse on its scheduled date.
//   5. Production: Day wise trackr's "Actual Production Ronch" / "Actual Production Amber" blocks,
//      daily, x 90% yield (rounded) = what's actually available to load. A day D's production
//      becomes dispatchable on D+1 (fixed one-day handoff lag), then further delayed by the
//      facility -> warehouse TAT matrix to compute its arrival day.
//   6. Each day, per facility, accumulate undispatched pool (carries over across days). While the
//      pool totals >= 500, form one truck using a strict (tier, size) ladder per warehouse x SKU:
//        tier 0    a committed PO would go unfulfilled — no floor, always first
//        tier 1..N below each DOI_LADDER rung (default 7/15/30/45 days of ambient-DRR cover)
//        tier N+1  past the top rung, or no ambient DRR at all — pile-gap toward WH_SPLIT
//                  (Bangalore 30% / Hyderabad 20% / Mumbai 20% / Gurgaon 20% / Kolkata 10%)
//        tier N+2  nothing needed anywhere — days sat at the plant, last-resort tiebreak
//      Tiers are compared lexicographically (see cmpKey), NOT packed into one number by magnitude
//      as they once were: with six tiers and deficits in the hundreds, magnitude packing bleeds
//      between bands and would eventually run past 2^53.
//   7. The destination is the warehouse with the BEST (lowest) tier reached by any SKU in the
//      facility's pool — deliberately not a sum across tiers, because that is what enforces the
//      ladder globally: nobody is topped toward 15 DOI while another warehouse sits below 7. Each
//      SKU is then filled only to the ceiling of the rung it is currently in, so a truck spreads
//      across whatever is most deprived rather than filling one warehouse all the way up. Only if
//      the ladder is satisfied and the truck is still under the 500-unit floor does it fall back to
//      loading whatever remains. EMERGENCY = tier 0 or 1; everything above is PILE-UP, and a
//      PILE-UP pick never repeats the same destination twice in a row — tracked globally across
//      both facilities (not per-facility), since two independent facility loops could otherwise
//      both pick the same warehouse on the same day, which isn't operationally realistic (a
//      warehouse can't absorb multiple full trucks of simultaneous inbound).
//
// Known simplifying assumptions (disclosed in the printed report, not silently hidden):
//   - DRR is held constant across the whole window (a trailing-15-day snapshot), not date-varying.
//   - Dark-store on-hand is not netted into the warehouse's opening balance — only its DRR is
//     added to the warehouse's outflow. Conservative (may slightly overstate near-term urgency).
//   - Trucks are single-destination, mixed-SKU, sized 500-725. A final undispatched residual under
//     500 at the end of the window is reported separately, not forced out.
//   - Facility SKU mix is read as-is from the sheet (no artificial Ronch/Amber SKU restriction).
//   - WH_SPLIT (target network share for pile-up balancing) is a business-set constant, distinct
//     from the actual trailing DRR split — don't conflate the two.

const fs = require('fs');

const args = process.argv.slice(2);
function flagValue(name) { const i = args.indexOf(name); return i !== -1 ? args[i + 1] : null; }

// --rows <payload.json>: { "<workbook>": { "<tab name>": [[...row], ...] } }, written by
// sync_sop_first_mile.py from the Sheets API with valueRenderOption=UNFORMATTED_VALUE. That render
// option is load-bearing, not incidental: Raw Data Sheet!B and Day wise trackr!A are read as Excel
// date SERIALS via toNum(), so a formatted string would silently become 0 and the row would vanish.
const rowsPath = flagValue('--rows');
// --json is optional on purpose: the suggestion engine re-runs this script as a child process to
// MEASURE a proposed production swap, and that child must not write over the parent's output. It
// reports back on stdout instead, exactly as it does in the skill.
const jsonOutPath = flagValue('--json');
if (!rowsPath) {
  console.error('Usage: node first_mile_dispatch.js --rows <payload.json> [--json <out.json>] [flags]');
  process.exit(1);
}
const PAYLOAD = JSON.parse(fs.readFileSync(rowsPath, 'utf8'));
// Mimics the xlsx workbook surface the parsers were written against: a `.Sheets` map keyed by tab
// name, so parseDrr's prefix discovery (Object.keys(...).find(...)) keeps working unchanged.
const mkWb = (book) => ({ Sheets: PAYLOAD[book] || {} });
// Seed each plant's pool with the "Current FG" figures from Dispatch Planning. On by default;
// --no-fg opts out (use when you know the FG cell is stale — it carries no date stamp).
const FG_ENABLED = !args.includes('--no-fg');
// Today's Day wise trackr production row is included by default (morning run: today's production
// hasn't finished/been counted yet, so FG doesn't reflect it). --no-today-production excludes it
// (evening run: today's production is already finished and current-inventory-sync has already
// folded it into the FG snapshot, so counting it again here would double-count it).
// Optionally scoped to one facility only: --no-today-production=AMBER excludes just that facility's
// today row (e.g. Amber's FG snapshot already folded it in, but Ronch's hasn't) while leaving the
// other facility's today production included. Bare --no-today-production excludes both, as before.
// (FACILITIES is defined further down; the filter against it happens right after that declaration.)
const noTodayProdArg = args.find(a => a === '--no-today-production' || a.startsWith('--no-today-production='));
// --include-hold makes "<facility> Hold" stock dispatchable from day one, on top of FG. Off by
// default because Hold is blocked stock; pass it only once the blocker is genuinely cleared.
const INCLUDE_HOLD = args.includes('--include-hold');
// Both plants compete for every destination on the same ladder. --no-joint reverts to the old
// sequential behaviour (all of Ronch's trucks, then all of Amber's) as an A/B baseline.
const JOINT_PLANTS = !args.includes('--no-joint');
// Production runs closer than this many days cannot realistically be changed — the line is already
// committed. Only runs STARTING on or after today+PROD_LEAD_DAYS are offered as swap candidates.
const PROD_LEAD_DAYS = Number(flagValue('--prod-lead-days')) || 10;
// --swap-run FACILITY:START:END:FROM>TO rewrites one production run before the simulation, e.g.
//   --swap-run "RONCH:2026-09-08:2026-09-09:M2 Pro>M1-2nd Gen"
// Used by the recommendation engine to measure a proposed change by actually re-running the plan.
const SWAP_RUN = flagValue('--swap-run');
// Suppresses the recommendation engine. Set on the child process the engine spawns, which is what
// stops it recursing forever.
const SUGGEST_ENABLED = !args.includes('--no-suggest');
// --extra-in-transit "FACILITY:WAREHOUSE:SKU:QTY;FACILITY:WAREHOUSE:SKU:QTY;..." manually injects
// additional in-transit stock not captured by Raw Data Sheet's FM dispatch records (a one-off,
// user-supplied assumption for a single run). Arrival day = today + facility->warehouse TAT,
// floored at WINDOW_START — same convention as computeInTransitArrivals's unmatched-remainder
// fallback, just keyed to the specific facility given instead of the worst-case of both.
const EXTRA_IN_TRANSIT = flagValue('--extra-in-transit');

const SKUS = ['M0', 'M1-2nd Gen', 'M1 Pro', 'M2 Pro', 'M3', 'M3 Pro'];
const SKU_ALIAS_MAP = {
  'M0': 'M0', 'NATIVE M0': 'M0',
  'M1': 'M1-2nd Gen', 'M1-2ND GEN': 'M1-2nd Gen', 'M1 AS': 'M1-2nd Gen', 'M1AS': 'M1-2nd Gen',
  'M1-AS': 'M1-2nd Gen', 'NATIVE M1 AS': 'M1-2nd Gen', 'M1 2ND GEN': 'M1-2nd Gen',
  'M1 PRO': 'M1 Pro', 'NATIVE M1 PRO': 'M1 Pro',
  'M2 PRO': 'M2 Pro', 'NATIVE M2 PRO': 'M2 Pro',
  'M3': 'M3', 'NATIVE M3': 'M3',
  'M3 PRO': 'M3 Pro', 'NATIVE M3 PRO': 'M3 Pro',
};
function normalizeSku(raw) {
  if (!raw && raw !== 0) return null;
  return SKU_ALIAS_MAP[String(raw).trim().toUpperCase()] || null;
}

const WH_ORDER = ['Bangalore', 'Gurgaon', 'Hyderabad', 'Mumbai', 'Kolkata'];
const FACILITIES = ['RONCH', 'AMBER'];

const TODAY_PRODUCTION_EXCLUDED_FACILITIES = noTodayProdArg
  ? (noTodayProdArg.includes('=')
      ? noTodayProdArg.split('=')[1].split(',').map(s => s.trim().toUpperCase()).filter(f => FACILITIES.includes(f))
      : FACILITIES.slice())
  : [];
const TODAY_PRODUCTION_ENABLED = TODAY_PRODUCTION_EXCLUDED_FACILITIES.length === 0;

const TAT = {
  RONCH: { Mumbai: 2, Hyderabad: 3, Bangalore: 4, Gurgaon: 5, Kolkata: 7 },
  AMBER: { Gurgaon: 2, Kolkata: 5, Bangalore: 6, Hyderabad: 6, Mumbai: 6 },
};

const PRODUCTION_YIELD = 0.9;
const TRUCK_MIN = 500, TRUCK_TARGET = 725;
const PO_CHANNELS = new Set(['Amazon', 'Flipkart', 'Croma', 'Vijay Sales', 'Reliance']);
// Age of a facility's pool for a SKU (days continuously non-zero) is tracked purely for visibility
// (see agedClearances below) — it's folded into skuScore() as the lowest-weight tiebreaker, not a
// mandatory override.
const MAX_HOLD_DAYS = 3;
// Business-set target network share per warehouse — NOT derived from DRR (actual DRR split now runs
// ~28/25/20/16/10). Used as the BOTTOM rung of the ladder below: once a warehouse×SKU is past the
// top DOI rung (or has no ambient DRR at all, so DOI is undefined), this is what keeps stock moving.
const WH_SPLIT = { Bangalore: 0.30, Hyderabad: 0.20, Mumbai: 0.20, Gurgaon: 0.20, Kolkata: 0.10 };

// ── DOI ladder ───────────────────────────────────────────────────────────────────────────
// Replenishment fills every warehouse×SKU to 7 days of cover before ANY of them gets 15, then 15
// before anyone gets 30, and so on. That is what makes stocking gradual and even instead of
// dumping a truck's worth into whichever warehouse happens to score highest.
//
// This replaces the old DRR_EMERGENCY_FLOOR_DAYS mechanism, which looked like a safety-stock
// policy but was a threshold on DEFICIT SIZE, not a stock level. Over the 8-day inclusive horizon
// it required `8·DRR − bal > 7·DRR`, i.e. `bal < DRR` — it only fired below ONE day of cover, which
// on live data was 1 of 28 warehouse×SKU pairs. Rung 1 below subsumes it and is strictly stronger.
//
// DOI is measured on ambient DRR only. Committed POs are already guaranteed by tier 0, so the
// ladder is safety stock ON TOP of committed orders. Calibration note: PO demand runs ~5.6x ambient
// DRR (954/day vs 171.7/day network-wide), so a 7-DOI rung is only ~1.2 days of real throughput —
// use --doi-ladder to lift the rungs if that buffer proves too thin.
const DOI_LADDER = (flagValue('--doi-ladder') || '7,15,30,45').split(',').map(Number).filter(n => n > 0);
const DOI_ENABLED = !args.includes('--no-doi'); // --no-doi reverts to pile-gap-only (A/B baseline)

// ── Lane preference ──────────────────────────────────────────────────────────────────────
// Every candidate destination must be judged over the SAME forward horizon. Previously the
// horizon was each candidate's own arrival date (day + TAT[f][wh]), so a longer lane walked more
// days, accumulated more PO/DRR demand and produced a LARGER apparent deficit — systematically
// pushing each plant toward its most DISTANT warehouses. Measured before this fix: Ronch pushed
// 3,965 M2 Pro units north to Gurgaon (5d) while Amber pushed 9,945 south (6d), the two flows
// crossing past each other, and plan-wide transit ran 52% above the lane floor.
const SCORING_HORIZON_DAYS = Number(flagValue('--horizon-days')) || 7; // worst TAT in the matrix
// Natural lane = the facility that reaches this warehouse fastest (Ronch: Mumbai/Hyderabad/
// Bangalore; Amber: Gurgaon/Kolkata).
const BEST_TAT = {};
for (const wh of WH_ORDER) BEST_TAT[wh] = Math.min(...FACILITIES.map(f => TAT[f][wh]));
const laneExcessDays = (f, wh) => TAT[f][wh] - BEST_TAT[wh];
// Each excess transit day discounts this destination's need by this fraction, so lane cost trades
// off against real need on one scale instead of being ignored. 0 disables the preference entirely
// (the A/B baseline). NB: flagValue() returns null when absent and `--lane-weight 0` is falsy, so
// this must test against null — `|| 0.15` would silently ignore the baseline flag.
// Default 1.0 chosen by sweeping 0 / 0.15 / 0.3 / 0.5 / 0.75 / 1.0 / 1.5 / 2 / 3 / 5 against live
// data: it minimised total projected stockout deficit (6,129 vs 7,859 at 0) while cutting transit
// from 42.5% to 32.3% above the lane floor. Above ~3 the preference turns too rigid and service
// degrades below baseline (8,169), which is exactly the failure mode this weight guards against.
const LANE_WEIGHT = flagValue('--lane-weight') !== null ? Number(flagValue('--lane-weight')) : 1.0;
// A proportional discount, NOT a flat units-per-day subtraction: flat subtraction can drive a
// small-but-real PO need to zero and demote it out of the emergency tier, causing a genuinely
// missed PO. This factor is bounded strictly above zero (~0.57 at the worst 5-day excess), so a
// warehouse with a real PO shortfall always keeps a non-zero tier-1 magnitude and still outranks
// every zero-need candidate. At 0.15 a far warehouse needs ~45% more need to win a 3-day-worse lane.
// Applied to EVERY SKU, not just the dual-sourced ones. Restricting it to SKUs both plants make
// (only M2 Pro) was tried and measurably lost on both axes — 45.0% above floor / 7,773 total deficit
// at its best, versus 32.3% / 6,129 unrestricted. The reason: a shorter lane is not merely a routing
// swap, it is a throughput gain — the same units cover demand days sooner — so preferring near
// warehouses helps even for a single-source SKU that only one plant can ship.
const laneFactor = (f, wh) => 1 / (1 + laneExcessDays(f, wh) * LANE_WEIGHT);

// Warehouse codes as they appear in Dispatch Planning / Raw Data Sheet (origin/facility columns)
const WH_CODE_MAP = {
  'PB-UC-BLR': 'Bangalore',
  'PB-UC-BOMBAY': 'Mumbai', 'PB-UC-MUM': 'Mumbai',
  'PB-UC-GGN': 'Gurgaon', 'PB-UC-GGN-PATAUDI': 'Gurgaon', 'PB-UC-GGN_PATAUDI': 'Gurgaon',
  'PB-UC-HYD': 'Hyderabad',
  'PB-UC-KOL': 'Kolkata', 'PB-UC-KOL-PANCHLA': 'Kolkata', 'PB-UC-KOL-PANCHALA': 'Kolkata',
};
// Production-dispatch destination codes use both PB-UC-* and short PB-* forms
const DISPATCH_DEST_MAP = Object.assign({}, WH_CODE_MAP, {
  'PB-GGN': 'Gurgaon', 'PB-BLR': 'Bangalore', 'PB-MUM': 'Mumbai', 'PB-KOL': 'Kolkata', 'PB-HYD': 'Hyderabad',
});
// Diwali Sales Plan tab's own warehouse-row codes (verified against the live sheet)
const DIWALI_WH_CODE_MAP = {
  'PB-UC-BLR': 'Bangalore', 'PB-UC-BOMBAY': 'Mumbai', 'PB-UC-GGN': 'Gurgaon',
  'PB-UC-HYD': 'Hyderabad', 'PB-UC-KOL': 'Kolkata',
};
const DS_PARENT_PREFIX = [
  ['PB-UC-BLR-', 'Bangalore'], ['PB-UC-DEL-', 'Gurgaon'], ['PB-UC-KOL-', 'Kolkata'],
  ['PB-UC-BOM-', 'Mumbai'], ['PB-UC-HYD-', 'Hyderabad'],
];
function darkStoreParent(code) {
  for (const [prefix, wh] of DS_PARENT_PREFIX) if (code.startsWith(prefix)) return wh;
  return null;
}

const PRODUCTION_ORIGINS = new Set(['AMBER', 'RONCH']);

// Raw Data Sheet column indices (0-based), verified against the live sheet 2026-09-17.
//
// Everything from "Type of Movement" rightward shifted +1 on 2026-09-06 when an unlabeled column
// was inserted after "Category" (col 6). parse_po_fulfillment.js was corrected at the time; this
// script was not, and read movement type from col 7 for eleven days. Col 7 holds "ETA for
// Warehouse", which is blank on virtually every row, so `movType === 'MM'` matched NOTHING: PO
// demand parsed as empty, tier 0 could never fire, every truck was routed on DOI ladder + pile-gap
// alone, and the run cheerfully reported "Every committed order is served in full" because it had
// read zero orders. Confirmed against run transcripts -- 03-Sep: 34,940 units ordered; 08-Sep: 0.
//
// Verified live: col 8 is the only column containing 'MM' (22 hits in the first 40 rows); the
// header row now agrees with the data, labelling col 8 "Type of Movement" and col 12 "Channel".
// Cols before the insertion point -- Date (1), Origin (4), Destination (5) -- are unaffected.
// Note col 20 is "M2AS", a SKU we don't track; M2 Pro is 21. Getting those two confused is exactly
// the off-by-one this comment exists to prevent.
const RDH_DATE_COL = 1, RDH_ORIGIN_COL = 4, RDH_DEST_COL = 5, RDH_MOVTYPE_COL = 8, RDH_CHANNEL_COL = 12;
const RDH_SKU_COLS = { 'M0': 18, 'M1-2nd Gen': 19, 'M2 Pro': 21, 'M1 Pro': 22, 'M3 Pro': 23, 'M3': 24 };
// PO identity, for the portal's at-risk list. Same indices parse_po_fulfillment.js uses, and the
// same caveat: these two columns' HEADERS are swapped relative to their data. The header over 37
// reads "PO/ Gate Pass Number" but the cells hold SO numbers (SO132370); the header over 38 reads
// "PO Expiry and Appointment Date" but the cells hold PO identifiers (PO/HP/26/08/692,
// FLSDDR2FR8JC). Verified live 2026-09-17 -- trust the data, not the labels.
const RDH_SO_NUM_COL = 37, RDH_PO_NUM_COL = 38;

function toNum(val) {
  if (val === null || val === undefined || val === '') return 0;
  const n = Number(String(val).replace(/,/g, ''));
  return isNaN(n) ? 0 : n;
}
// With the --rows adapter a "worksheet" IS already an array of row arrays, so this is the single
// seam where the xlsx dependency used to live. Rows are padded to the widest row because the
// Sheets API omits trailing empty cells per row, while xlsx padded every row to the sheet range --
// several parsers loop to `rows[i].length`, and a short header row would hide real columns.
function sheetToRows(ws) {
  if (!Array.isArray(ws) || !ws.length) return [];
  const width = ws.reduce((w, r) => Math.max(w, (r || []).length), 0);
  return ws.map((r) => {
    const row = (r || []).slice();
    while (row.length < width) row.push(null);
    return row;
  });
}
function serialToYMD(serial) { return new Date((serial - 25569) * 86400 * 1000).toISOString().slice(0, 10); }
function ymdToSerial(ymd) {
  const [y, m, d] = ymd.split('-').map(Number);
  return Math.round((Date.UTC(y, m - 1, d) / 86400000) + 25569);
}
function ymdToLabel(ymd) {
  const [y, m, d] = ymd.split('-');
  return new Date(Date.UTC(+y, +m - 1, +d)).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
}
function addDaysYMD(ymd, days) {
  const [y, m, d] = ymd.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}
function fmt0(n) { return n === null || n === undefined ? 'n/a' : Math.round(n).toString(); }
function fmt1(n) { return n === null || n === undefined ? 'n/a' : (Math.round(n * 10) / 10).toString(); }

const todaySerial = Math.floor((Date.now() + 19800000) / 86400000) + 25569;
const todayYMD = serialToYMD(todaySerial);
const nowIST = new Date(Date.now() + 19800000).toISOString().replace('T', ' ').slice(0, 16) + ' IST';

const WINDOW_START = flagValue('--from') || addDaysYMD(todayYMD, 1); // first dispatch day = tomorrow
const WINDOW_END = flagValue('--to') || addDaysYMD(WINDOW_START, 14); // default: 15-day rolling window

// Production days that actually feed this window: a day D's output only ships on D+1, so the run
// consumes WINDOW_START-1 .. WINDOW_END-1. Production dated after PROD_LAST exists in the sheet
// (it runs to 31-Oct) but this plan can never load it onto a truck — anything reading the
// production calendar must respect these bounds or it will "see" resupply that never arrives.
const PROD_FIRST = addDaysYMD(WINDOW_START, -1), PROD_LAST = addDaysYMD(WINDOW_END, -1);
const inProdWindow = ymd => ymd >= PROD_FIRST && ymd <= PROD_LAST;

// Trucks leaving on the last window day still take up to 7 days to land (Kolkata), so demand is
// counted a little past the window or those arrivals get judged against no orders at all.
const PO_CUTOFF_BUFFER_DAYS = Number(flagValue('--po-cutoff-buffer')) || 7;
// PO demand is counted only up to this date. The run consumes production to PROD_LAST, so counting
// orders that only later production could serve scores the plan against demand it was never given
// the stock to meet — that asymmetry made M1-2nd Gen look 6,558 units short when the honest
// in-window figure is 3,036. Default sits a few days past WINDOW_END so trucks leaving on the last
// window day still have their arrivals (up to a 7-day Kolkata lane) judged against real orders.
// Derived from the window, not hardcoded: the skill pins this to a literal date, which is fine for
// a human running it ad hoc but not for a cron, which would sail past it and silently count zero PO
// demand from that day on -- the same class of silent-zero failure as the column drift.
const PO_CUTOFF = flagValue('--po-cutoff') || addDaysYMD(WINDOW_END, PO_CUTOFF_BUFFER_DAYS);
// Balances are tracked exactly as far as demand is counted — no further.
const SIM_BUFFER_END = PO_CUTOFF > WINDOW_END ? PO_CUTOFF : WINDOW_END;

// ═══════════════════════════════════════════════════════════════════════
// Parse: Dispatch Planning tab (Copy Daily Input Anish) — on-hand + in-transit
// ═══════════════════════════════════════════════════════════════════════
function parseDispatchSheetWs(ws) {
  const rows = sheetToRows(ws);
  let headerRowIdx = -1, invFacCol = -1, itFacCol = -1;
  const invSkuCols = {}, itSkuCols = {};
  for (let i = 0; i < Math.min(10, rows.length); i++) {
    const row = rows[i];
    let facilityCount = 0, skuCount = 0;
    const tmpInvSku = {}, tmpItSku = {};
    let tmpInvFac = -1, tmpItFac = -1;
    for (let c = 0; c < row.length; c++) {
      const s = String(row[c] || '').trim();
      if (s.toUpperCase() === 'FACILITY' || s.toUpperCase() === 'TO') {
        if (facilityCount === 0) { tmpInvFac = c; facilityCount++; }
        else if (facilityCount === 1) { tmpItFac = c; facilityCount++; }
      }
      const sku = normalizeSku(s);
      if (sku) {
        if (tmpItFac === -1) { if (!tmpInvSku[sku]) tmpInvSku[sku] = c; }
        else { if (!tmpItSku[sku]) tmpItSku[sku] = c; }
        skuCount++;
      }
    }
    if (skuCount >= 4 && facilityCount >= 1) {
      headerRowIdx = i; invFacCol = tmpInvFac; itFacCol = tmpItFac;
      Object.assign(invSkuCols, tmpInvSku); Object.assign(itSkuCols, tmpItSku);
      break;
    }
  }
  if (headerRowIdx === -1) { console.error('Dispatch Planning header not found'); return null; }

  const inventoryView = {}, intransit = {};
  for (let i = headerRowIdx + 1; i < rows.length; i++) {
    const row = rows[i];
    const facVal = String(row[invFacCol] || '').trim().toUpperCase();
    if (!facVal || facVal === 'GRAND TOTAL') { if (facVal === 'GRAND TOTAL') break; continue; }
    const city = WH_CODE_MAP[facVal];
    if (!city) continue;
    if (!inventoryView[city]) inventoryView[city] = {};
    if (!intransit[city]) intransit[city] = {};
    for (const sku of SKUS) {
      const col = invSkuCols[sku];
      if (col !== undefined) inventoryView[city][sku] = (inventoryView[city][sku] || 0) + (toNum(row[col]) || 0);
      const col2 = itSkuCols[sku];
      if (col2 !== undefined) intransit[city][sku] = (intransit[city][sku] || 0) + (toNum(row[col2]) || 0);
    }
  }
  return { inventoryView, intransit };
}

// ═══════════════════════════════════════════════════════════════════════
// Parse: WH-Channel-SKU → "Current Inventory" tab → the plant FG/Hold table
// ═══════════════════════════════════════════════════════════════════════
// A proper table (label column, one SKU per column), currently around K91:Q95:
//     (header)   | M0 | M1-2nd Gen | M1 Pro | M2 Pro | M3 | M3 Pro   <- shared with the table above
//     Ronch FG   |    |        250 |        |     50 |    |    100
//     Ronch Hold |    |      1,188 |      9 |    432 | 25 |     31
//     Amber FG   | 270|            |    600 |    626 |    |
//     total      | 270|      1,438 |    609 |  1,108 | 25 |    131
//
// ONLY the "<facility> FG" rows feed the plant pool. "<facility> Hold" is stock that exists but is
// deliberately not dispatchable — captured separately so the run can SAY how much it excluded,
// rather than that stock silently vanishing from the picture.
//
// This replaces a free-text `Current FG` cell in Copy Daily Input Anish's Dispatch Planning tab,
// which was deleted from that sheet on 28-Aug-2026 and had no timestamp. Scoped deliberately to the
// Current Inventory tab: the label "FG + Hold" also appears in `Diwali Sales Plan - Overall` (three
// times) and `Copy of MoM trending`, where it heads entirely different tables.
function parseCurrentFG(wbWH) {
  const fg = {}, hold = {}; const unresolved = [];
  for (const f of FACILITIES) { fg[f] = {}; hold[f] = {}; }
  const ws = wbWH.Sheets['Current Inventory'];
  if (!ws) { console.warn('⚠️  "Current Inventory" tab not found — plant pools will open empty.'); return { fg, hold, unresolved, found: false }; }
  const rows = sheetToRows(ws);

  // Anchor on the STOCK ROWS, not on the "FG + HOLD" caption.
  //
  // This used to search for a cell reading exactly "FG + HOLD" and give up if it wasn't found.
  // That caption was removed from the tab at some point and the parser silently returned empty
  // pools from then on -- every run opened both plants at zero and quietly planned without the
  // ~1,000 units of finished goods actually sitting at Ronch and Amber (verified 2026-09-17:
  // Ronch FG 653, Amber FG 401). A decorative label nobody knew was load-bearing is a bad thing
  // to hang the parse on, so now we find the "<facility> FG" / "<facility> Hold" rows themselves,
  // which are the actual data and can't be deleted without the table ceasing to exist.
  const STOCK_ROW_RE = /^(.+?)\s+(FG|Hold)$/i;
  const isStockRow = (v) => {
    const m = String(v || '').trim().match(STOCK_ROW_RE);
    return !!m && FACILITIES.includes(m[1].trim().toUpperCase());
  };

  let firstRow = -1, labelCol = -1;
  for (let i = 0; i < rows.length && firstRow === -1; i++) {
    for (let c = 0; c < rows[i].length; c++) {
      if (isStockRow(rows[i][c])) { firstRow = i; labelCol = c; break; }
    }
  }
  if (firstRow === -1) {
    console.warn('⚠️  No "<facility> FG"/"<facility> Hold" rows found in Current Inventory — plant pools will open empty.');
    return { fg, hold, unresolved, found: false };
  }

  // SKU columns come from the nearest header row ABOVE the stock rows that names at least three
  // SKUs to the right of the label column. On the live sheet that's the same `To | M0 | M1AS | ...`
  // header the channel table above already uses (one header, two tables stacked under it), which is
  // why the stock table has no header of its own any more.
  const skuCols = {};
  for (let i = firstRow - 1; i >= 0 && Object.keys(skuCols).length < 3; i--) {
    const found = {};
    for (let c = labelCol + 1; c < (rows[i] || []).length; c++) {
      const sku = normalizeSku(rows[i][c]);
      if (sku && found[sku] === undefined) found[sku] = c;
    }
    if (Object.keys(found).length >= 3) Object.assign(skuCols, found);
  }
  if (!Object.keys(skuCols).length) {
    console.warn('⚠️  Found plant stock rows but no SKU header above them — plant pools will open empty.');
    return { fg, hold, unresolved, found: false };
  }

  const totalRow = {};
  for (let i = firstRow; i < rows.length; i++) {
    const label = String(rows[i][labelCol] || '').trim();
    if (!label) break;                                  // blank label ends the table
    const readRow = () => {
      const o = {};
      for (const sku of SKUS) { const c = skuCols[sku]; if (c !== undefined) o[sku] = toNum(rows[i][c]) || 0; }
      return o;
    };
    if (/^total$/i.test(label)) { Object.assign(totalRow, readRow()); continue; }
    const m = label.match(/^(.+?)\s+(FG|Hold)$/i);
    // Never drop an unrecognised row silently — a renamed facility or a new row type would
    // otherwise quietly understate the pool.
    if (!m) { unresolved.push(`row "${label}"`); continue; }
    const fac = m[1].trim().toUpperCase();
    if (!FACILITIES.includes(fac)) { unresolved.push(`unknown facility "${label}"`); continue; }
    const bucket = /fg/i.test(m[2]) ? fg : hold;
    const vals = readRow();
    for (const sku of SKUS) if (vals[sku]) bucket[fac][sku] = (bucket[fac][sku] || 0) + vals[sku];
  }

  // The table publishes its own total (FG + Hold). If our parse disagrees, a row type has been
  // added or the layout has drifted — say so rather than quietly under-counting.
  if (Object.keys(totalRow).length) {
    const bad = [];
    for (const sku of SKUS) {
      const mine = FACILITIES.reduce((s, f) => s + (fg[f][sku] || 0) + (hold[f][sku] || 0), 0);
      if (Math.abs(mine - (totalRow[sku] || 0)) > 0.5) bad.push(`${sku}: parsed ${fmt0(mine)} vs sheet total ${fmt0(totalRow[sku] || 0)}`);
    }
    if (bad.length) {
      console.warn('⚠️  Plant FG/Hold table does not reconcile against its own total row:');
      for (const b of bad) console.warn('     ' + b);
    }
  }
  return { fg, hold, unresolved, found: true };
}

// ═══════════════════════════════════════════════════════════════════════
// Parse: Raw Data Sheet — production dispatch records (FM, AMBER/RONCH origin), PO demand (MM)
// ═══════════════════════════════════════════════════════════════════════
function parseRawDataSheet(wbDI) {
  const ws = wbDI.Sheets['Raw Data Sheet'];
  if (!ws) { console.error('Cannot find Raw Data Sheet tab'); return { dispatchRecords: [], poDemandByDay: {} }; }
  const rows = sheetToRows(ws);

  const dispatchRecords = []; // { dateSerial, facility, wh, sku, qty }
  const poDemandByDay = {}; // ymd -> wh -> sku -> qty
  // Deliberately a SEPARATE structure rather than turning the leaf above into an array: five
  // consumers (getPoDemand, demandOf, poAfter, mixRows, poOrdered) read that leaf as a number and
  // would break silently. This carries the per-row identity the aggregate throws away, in sheet
  // order, so a bucket shortfall can be attributed back to the actual orders that make it up.
  const poRowsByDay = {}; // ymd -> wh -> sku -> [{ qty, poNumber, soNumber, channel }]

  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    const movType = String(row[RDH_MOVTYPE_COL] || '').trim();
    const originRaw = String(row[RDH_ORIGIN_COL] || '').trim().toUpperCase();
    const dateSerial = toNum(row[RDH_DATE_COL]);
    if (!dateSerial) continue;

    if (PRODUCTION_ORIGINS.has(originRaw)) {
      const destRaw = String(row[RDH_DEST_COL] || '').trim().toUpperCase();
      const wh = DISPATCH_DEST_MAP[destRaw];
      if (!wh) continue;
      for (const sku of SKUS) {
        const col = RDH_SKU_COLS[sku];
        const qty = col !== undefined ? toNum(row[col]) : 0;
        if (qty > 0) dispatchRecords.push({ dateSerial, facility: originRaw, wh, sku, qty });
      }
      continue;
    }

    if (movType !== 'MM') continue;
    const channel = String(row[RDH_CHANNEL_COL] || '').trim();
    if (!PO_CHANNELS.has(channel)) continue;
    const wh = WH_CODE_MAP[originRaw];
    if (!wh) continue;
    const ymd = serialToYMD(dateSerial);
    // Capped at PO_CUTOFF here, at the source, so every downstream consumer (poNeed scoring, the
    // balance advance, the fill-rate audit) sees one consistent demand set without repeating the test.
    if (ymd < WINDOW_START || ymd > PO_CUTOFF) continue;
    poDemandByDay[ymd] = poDemandByDay[ymd] || {};
    poDemandByDay[ymd][wh] = poDemandByDay[ymd][wh] || {};
    poRowsByDay[ymd] = poRowsByDay[ymd] || {};
    poRowsByDay[ymd][wh] = poRowsByDay[ymd][wh] || {};
    const poNumber = String(row[RDH_PO_NUM_COL] || '').trim();
    const soNumber = String(row[RDH_SO_NUM_COL] || '').trim();
    for (const sku of SKUS) {
      const col = RDH_SKU_COLS[sku];
      const qty = col !== undefined ? toNum(row[col]) : 0;
      if (qty > 0) {
        poDemandByDay[ymd][wh][sku] = (poDemandByDay[ymd][wh][sku] || 0) + qty;
        // One sheet row can carry several SKUs, so one PO legitimately appears in several buckets.
        // Per-PO-per-SKU is the right grain -- same as parse_po_fulfillment.js.
        (poRowsByDay[ymd][wh][sku] = poRowsByDay[ymd][wh][sku] || []).push({ qty, poNumber, soNumber, channel });
      }
    }
  }

  // An empty PO set is indistinguishable, downstream, from "every order is comfortably served" --
  // tier 0 simply never fires and the fill-rate audit prints a green all-clear. That is exactly how
  // the 2026-09-06 column shift went unnoticed for eleven days (see RDH_* above). A genuinely
  // order-free 15-day window is not a thing, so say so loudly rather than planning on thin air.
  const poRowCount = Object.keys(poDemandByDay).length;
  if (poRowCount === 0) {
    console.log(`\n  ⚠️  NO PO DEMAND PARSED from 'Raw Data Sheet' for ${WINDOW_START}..${PO_CUTOFF}.`);
    console.log(`      Every fill-rate number below will read 100% because nothing was read, NOT`);
    console.log(`      because every order is served. Check the RDH_* column indices against the`);
    console.log(`      live sheet before trusting this run -- col ${RDH_MOVTYPE_COL} should hold 'MM'.\n`);
  }

  return { dispatchRecords, poDemandByDay, poRowsByDay };
}

// Backtrack already-in-transit stock to an arrival day (most-recent-dispatch-first, matched
// against Dispatch Planning's aggregate in-transit total) — same technique as
// parse_warehouse_stock_projection.js's computeInTransitETAs.
function computeInTransitArrivals(inTransit, dispatchRecords) {
  const byWhSku = {};
  for (const wh of WH_ORDER) { byWhSku[wh] = {}; for (const sku of SKUS) byWhSku[wh][sku] = []; }
  for (const rec of dispatchRecords) byWhSku[rec.wh][rec.sku].push(rec);
  for (const wh of WH_ORDER) for (const sku of SKUS) byWhSku[wh][sku].sort((a, b) => b.dateSerial - a.dateSerial);

  const arrivals = {}; // ymd -> wh -> sku -> qty
  const maxTransit = wh => Math.max(TAT.AMBER[wh], TAT.RONCH[wh]);
  // First day the balance simulation actually reads arrivals for — anything landing earlier must be
  // pulled forward to this day or it is dropped on the floor (see the clamp below).
  const firstSimSerial = ymdToSerial(WINDOW_START);

  for (const wh of WH_ORDER) {
    for (const sku of SKUS) {
      let needed = (inTransit[wh] || {})[sku] || 0;
      if (needed <= 0) continue;
      for (const rec of byWhSku[wh][sku]) {
        if (needed <= 0) break;
        const taken = Math.min(rec.qty, needed);
        const tat = TAT[rec.facility][wh];
        let arrivalSerial = rec.dateSerial + tat;
        // Overdue stock clamps to the FIRST SIMULATED DAY, not to today. The simulation loop starts
        // at WINDOW_START (= today+1 by default) and never reads a today-dated arrival, so clamping
        // to today silently deleted this stock from the projection entirely (confirmed real bug:
        // 1,372 units of Bangalore-bound in-transit vanished, badly overstating Bangalore's urgency).
        if (arrivalSerial < firstSimSerial) arrivalSerial = firstSimSerial;
        const ymd = serialToYMD(arrivalSerial);
        arrivals[ymd] = arrivals[ymd] || {};
        arrivals[ymd][wh] = arrivals[ymd][wh] || {};
        arrivals[ymd][wh][sku] = (arrivals[ymd][wh][sku] || 0) + taken;
        needed -= taken;
      }
      if (needed > 0) {
        // unmatched remainder — fall back to today + worst-case transit (floored at the first
        // simulated day for the same reason as the clamp above)
        const ymd = serialToYMD(Math.max(todaySerial + maxTransit(wh), firstSimSerial));
        arrivals[ymd] = arrivals[ymd] || {};
        arrivals[ymd][wh] = arrivals[ymd][wh] || {};
        arrivals[ymd][wh][sku] = (arrivals[ymd][wh][sku] || 0) + needed;
      }
    }
  }
  return arrivals;
}

// ═══════════════════════════════════════════════════════════════════════
// Parse: Diwali Sales Plan - Overall tab — trailing 15-day DRR (warehouse rows + dark-store rows)
// ═══════════════════════════════════════════════════════════════════════
function parseDrr(wbWH) {
  const sheetName = Object.keys(wbWH.Sheets).find(n => n.trim().toLowerCase().startsWith('diwali sales plan'));
  if (!sheetName) { console.error('Cannot find Diwali Sales Plan - Overall tab'); return { whDrr: {}, dsDrr: {} }; }
  const ws = wbWH.Sheets[sheetName];
  const rows = sheetToRows(ws);

  let headerRow = -1, labelCol = -1;
  for (let i = 0; i < rows.length; i++) {
    for (let c = 0; c < rows[i].length; c++) {
      if (String(rows[i][c] || '').trim() === 'Channel / Facility') { headerRow = i; labelCol = c; break; }
    }
    if (headerRow !== -1) break;
  }
  if (headerRow === -1) { console.error('Cannot find "Channel / Facility" DRR header'); return { whDrr: {}, dsDrr: {} }; }

  const skuLabelRow = rows[headerRow];
  const skuCols = [];
  for (let c = labelCol + 1; c < skuLabelRow.length && skuCols.length < 6; c++) {
    const sku = normalizeSku(skuLabelRow[c]);
    if (sku) skuCols.push({ col: c, sku }); else break;
  }

  const whDrr = {}; for (const wh of WH_ORDER) { whDrr[wh] = {}; for (const sku of SKUS) whDrr[wh][sku] = 0; }
  const dsDrr = {}; // dsCode -> { parent, skus }

  for (let i = headerRow + 1; i < rows.length; i++) {
    const label = String(rows[i][labelCol] || '').trim();
    if (!label) continue;
    const wh = DIWALI_WH_CODE_MAP[label];
    const dsParent = darkStoreParent(label);
    if (!wh && !dsParent) continue;
    const skus = {};
    for (const { col, sku } of skuCols) skus[sku] = toNum(rows[i][col]);
    if (wh) whDrr[wh] = skus;
    else dsDrr[label] = { parent: dsParent, skus };
  }
  return { whDrr, dsDrr };
}

function effectiveDrr(whDrr, dsDrr) {
  const eff = {};
  for (const wh of WH_ORDER) { eff[wh] = {}; for (const sku of SKUS) eff[wh][sku] = whDrr[wh][sku] || 0; }
  for (const [ds, { parent, skus }] of Object.entries(dsDrr)) {
    if (!parent) continue;
    for (const sku of SKUS) eff[parent][sku] += skus[sku] || 0;
  }
  return eff;
}

// ═══════════════════════════════════════════════════════════════════════
// Parse: Day wise trackr — daily Actual Production Ronch / Actual Production Amber blocks
// ═══════════════════════════════════════════════════════════════════════
function parseDailyProduction(wbWH) {
  const ws = wbWH.Sheets['Day wise trackr'];
  if (!ws) { console.error('Cannot find Day wise trackr tab'); return {}; }
  const rows = sheetToRows(ws);

  let blockHeaderRow = -1;
  const facilityCol = {};
  for (let i = 0; i < Math.min(10, rows.length); i++) {
    for (let c = 0; c < rows[i].length; c++) {
      const label = String(rows[i][c] || '').trim();
      if (label === 'Actual Production Ronch') { facilityCol.RONCH = c; blockHeaderRow = i; }
      if (label === 'Actual Production Amber') { facilityCol.AMBER = c; blockHeaderRow = i; }
      // Read for cross-checking only — it is the sum of the two facility blocks and carries no
      // plant attribution, so it can never drive dispatch. See the reconciliation below.
      if (label === 'Combined Actual Production') { facilityCol.COMBINED = c; blockHeaderRow = i; }
    }
    if (facilityCol.RONCH !== undefined && facilityCol.AMBER !== undefined) break;
  }
  if (facilityCol.RONCH === undefined || facilityCol.AMBER === undefined) {
    console.error('Cannot find Actual Production Ronch/Amber headers'); return {};
  }

  const skuLabelRow = rows[blockHeaderRow + 1];
  function findSkuCols(baseCol) {
    const cols = [];
    for (let c = baseCol; c < skuLabelRow.length && cols.length < 6; c++) {
      const sku = normalizeSku(skuLabelRow[c]);
      if (sku) cols.push({ col: c, sku }); else break;
    }
    return cols;
  }
  const ronchCols = findSkuCols(facilityCol.RONCH);
  const amberCols = findSkuCols(facilityCol.AMBER);
  const combinedCols = facilityCol.COMBINED !== undefined ? findSkuCols(facilityCol.COMBINED) : [];

  const result = {}; // ymd -> RONCH/AMBER/COMBINED -> sku -> qty
  for (let i = blockHeaderRow + 2; i < rows.length; i++) {
    const row = rows[i];
    const dateSerial = toNum(row[0]);
    if (!dateSerial) continue;
    const ymd = serialToYMD(dateSerial);
    result[ymd] = { RONCH: {}, AMBER: {}, COMBINED: {} };
    for (const { col, sku } of ronchCols) result[ymd].RONCH[sku] = toNum(row[col]);
    for (const { col, sku } of amberCols) result[ymd].AMBER[sku] = toNum(row[col]);
    for (const { col, sku } of combinedCols) result[ymd].COMBINED[sku] = toNum(row[col]);
  }
  return result;
}

// ═══════════════════════════════════════════════════════════════════════
// Main
// ═══════════════════════════════════════════════════════════════════════
const wbDI = mkWb('copy_daily_input_anish');
const wbWH = mkWb('wh_channel_sku');

const dispatchSheet = parseDispatchSheetWs(wbDI.Sheets['Dispatch Planning']);
const { dispatchRecords, poDemandByDay, poRowsByDay } = parseRawDataSheet(wbDI);
const { whDrr, dsDrr } = parseDrr(wbWH);
const effDrr = effectiveDrr(whDrr, dsDrr);
const dailyProduction = parseDailyProduction(wbWH);
// Snapshot today's row BEFORE the exclusion below deletes or zeroes it. The portal shows today's
// production on both scenarios -- it is a fact about the plant either way; the toggle only decides
// whether it is treated as already-in-FG or as additional dispatchable supply. Raw, pre-yield, same
// as the sheet; the yielded figure is Math.round(q * PRODUCTION_YIELD), applied at point of use.
const TODAY_PRODUCTION_SNAPSHOT = JSON.parse(JSON.stringify(dailyProduction[todayYMD] || {}));
if (TODAY_PRODUCTION_EXCLUDED_FACILITIES.length && dailyProduction[todayYMD]) {
  if (TODAY_PRODUCTION_EXCLUDED_FACILITIES.length === FACILITIES.length) {
    delete dailyProduction[todayYMD];
  } else {
    // Partial exclusion: zero out just the named facility's today row, leaving the other facility's
    // untouched. Note PROD_FIRST == WINDOW_START-1 == todayYMD, so today IS inside the facility-vs-
    // COMBINED reconciliation window below — mirror the same subtraction into COMBINED (same fix as
    // --swap-run's COMBINED mirroring above) so this edit doesn't trip a false "blocks unmaintained"
    // warning against the sheet's own COMBINED total, which still includes the excluded facility.
    for (const fac of TODAY_PRODUCTION_EXCLUDED_FACILITIES) {
      const d = dailyProduction[todayYMD][fac];
      if (!d) continue;
      const c = dailyProduction[todayYMD].COMBINED;
      if (c) for (const [sku, q] of Object.entries(d)) c[sku] = Math.max(0, (c[sku] || 0) - (q || 0));
      dailyProduction[todayYMD][fac] = {};
    }
  }
}

// Which SKUs each facility actually produces in this window. Used by the LANE EFFICIENCY report to
// separate a slow-lane truck that was a real routing CHOICE (the other plant makes that SKU too —
// in practice only M2 Pro) from one that was FORCED (single-source: M1 Pro is Amber-only, M1-2nd Gen
// and M3 Pro are Ronch-only). Forced volume is physics, not a planning failure, and must not read
// as one. Note this split is for reporting only — the lane discount itself deliberately applies to
// every SKU, see laneFactor().
const facilityMakes = {};
for (const f of FACILITIES) facilityMakes[f] = new Set();
for (const dayMap of Object.values(dailyProduction)) {
  for (const f of FACILITIES) {
    for (const [sku, qty] of Object.entries((dayMap || {})[f] || {})) if (qty > 0) facilityMakes[f].add(sku);
  }
}
const otherFacility = f => (f === 'RONCH' ? 'AMBER' : 'RONCH');

// ── Production calendar ──────────────────────────────────────────────────────────────────
// Production is NOT continuous. Ronch's M1-2nd Gen stops after 15-Sep and Amber's M2 Pro stops the
// same day, yet ~6,150 units of M1-2nd Gen and ~4,850 of M2 Pro PO demand land AFTER those dates.
// With a flat 7-day scoring horizon the planner cannot see a 25-Sep order on 5-Sep, so it spends
// the last of the stock climbing the DOI ladder and the order then has nothing to draw on — the
// likely cause of M1-2nd Gen closing at -1,032 (Bangalore) despite a window surplus of 795 units.
// Apply --swap-run before anything reads the production calendar, so the whole simulation — pool
// seeding, horizons, scarcity, reporting — sees the swapped plan as if it were the real one.
if (SWAP_RUN) {
  const m = String(SWAP_RUN).match(/^([A-Za-z]+):(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2}):(.+?)>(.+)$/);
  if (!m) {
    console.error('⚠️  --swap-run must look like FACILITY:START:END:FROM>TO — ignoring: ' + SWAP_RUN);
  } else {
    const [, facRaw, start, end, fromRaw, toRaw] = m;
    const fac = facRaw.trim().toUpperCase();
    const from = normalizeSku(fromRaw), to = normalizeSku(toRaw);
    let moved = 0;
    if (FACILITIES.includes(fac) && from && to) {
      for (const ymd of Object.keys(dailyProduction)) {
        if (ymd < start || ymd > end) continue;
        const d = (dailyProduction[ymd] || {})[fac];
        if (!d) continue;
        const q = d[from] || 0;
        if (q > 0) {
          d[to] = (d[to] || 0) + q; d[from] = 0; moved += q;
          // Mirror the move into COMBINED. Combined is defined as Ronch + Amber, so rewriting only
          // the facility block leaves the two out of step and the reconciliation check below fires
          // on a plan we ourselves just edited. That false warning used to escape the suggestion
          // engine's child process into the parent's report, telling the dispatch team their plan
          // "may be built on incomplete production data" while printing 44300 vs 44300.
          const c = (dailyProduction[ymd] || {}).COMBINED;
          if (c) { c[to] = (c[to] || 0) + q; c[from] = Math.max(0, (c[from] || 0) - q); }
        }
      }
    }
    console.log(`↻ --swap-run applied: ${fac} ${start}..${end}  ${from} → ${to}  (${fmt0(moved)} units moved)`);
  }
}

const prodDaysSorted = Object.keys(dailyProduction).sort();

// `Day wise trackr` carries three parallel blocks: Combined Actual Production (Q:W) and the two
// facility blocks (Ronch X:AD, Amber AE:AK). We read the facility blocks — dispatch has to know
// which plant holds the stock — and Combined should simply be their sum. It is, from Aug-2026
// onward. But the facility blocks were left unmaintained through Jun–Jul 2026, where Combined
// holds ~49,683 units they do not, and nothing surfaced that. Reconcile over the dates this run
// actually consumes and say so loudly if they diverge; do NOT silently fall back to Combined,
// because without a facility attribution the figure is unusable for dispatch.
{
  const gaps = [];
  let cSum = 0, sSum = 0;
  for (const ymd of prodDaysSorted) {
    if (!inProdWindow(ymd)) continue;
    const d = dailyProduction[ymd] || {};
    for (const sku of SKUS) {
      const c = ((d.COMBINED || {})[sku] || 0);
      const s = ((d.RONCH || {})[sku] || 0) + ((d.AMBER || {})[sku] || 0);
      cSum += c; sSum += s;
      if (Math.abs(c - s) > 0.5) gaps.push({ ymd, sku, c, s });
    }
  }
  if (gaps.length) {
    console.warn(`\n⚠️  PRODUCTION BLOCK MISMATCH — "Combined Actual Production" (${fmt0(cSum)}) does not equal`);
    console.warn(`   Ronch + Amber (${fmt0(sSum)}) over ${ymdToLabel(PROD_FIRST)}..${ymdToLabel(PROD_LAST)}. The facility split is the`);
    console.warn('   authoritative input for dispatch, so this plan may be built on incomplete production data:');
    for (const g of gaps.slice(0, 12)) console.warn(`     ${ymdToLabel(g.ymd)}  ${pad(g.sku, 13)} combined ${fmt0(g.c)}  vs  split ${fmt0(g.s)}`);
    if (gaps.length > 12) console.warn(`     ...and ${gaps.length - 12} more`);
  }
}
// First day >= fromYMD on which this facility actually makes this SKU; null if never again.
// BOUNDED BY inProdWindow: the sheet carries production through 31-Oct, but this run can only ever
// load output dated <= PROD_LAST onto a truck. Scanning unbounded made the horizon logic "see"
// resupply that never arrives — M2 Pro's last usable run is 15-Sep, but the scan found 26-Sep
// production and kept the horizon at 7 days for precisely the SKU that most needed it widened.
function nextProductionDay(f, sku, fromYMD) {
  for (const ymd of prodDaysSorted) {
    if (ymd < fromYMD || !inProdWindow(ymd)) continue;
    if ((((dailyProduction[ymd] || {})[f] || {})[sku] || 0) > 0) return ymd;
  }
  return null;
}
// Soonest that freshly-made stock of this SKU could actually be standing in ANY warehouse: the next
// production day, +1 because a day's output is only dispatchable the following day, + that lane's
// transit. Infinity when nothing is scheduled anywhere — i.e. what exists now is all there will be.
function daysUntilResupplyCouldLand(sku, fromYMD) {
  let best = Infinity;
  for (const f of FACILITIES) {
    const p = nextProductionDay(f, sku, fromYMD);
    if (!p) continue;
    const dispatchable = ymdToSerial(p) + 1;
    for (const wh of WH_ORDER) best = Math.min(best, dispatchable + TAT[f][wh] - ymdToSerial(fromYMD));
  }
  return best;
}
// Earliest a FOLLOWING truck could land this SKU at this warehouse. A further truck can leave
// tomorrow at the soonest, and only from a facility that will actually have stock — either already
// in its pool, or from its next production run. null when no facility can ever resupply.
// (`pool` is initialised later in the file; this is only ever called from inside the simulation.)
function nextResupplyArrivalYMD(wh, sku, day) {
  let best = null;
  for (const f of FACILITIES) {
    const candidates = [];
    if (pool[f][sku] > 0) candidates.push(addDaysYMD(day, 1));       // stock on hand: truck tomorrow
    const p = nextProductionDay(f, sku, day);
    if (p) candidates.push(addDaysYMD(p, 1));                        // otherwise gated by the next run
    if (!candidates.length) continue;
    const dispatchDay = candidates.sort()[0];
    const arrival = addDaysYMD(dispatchDay, TAT[f][wh]);
    if (!best || arrival < best) best = arrival;
  }
  return best;
}
// PO urgency horizon for this SKU. Only ever EXTENDS past SCORING_HORIZON_DAYS (the max() guarantees
// it never shrinks), and is capped at the simulation end. While production continues this resolves
// to the usual 7 days and nothing changes; once production has ended it stretches far enough to see
// every remaining committed order, so tier 0 — which dominates every DOI rung by construction — holds
// the stock back automatically. No separate reservation mechanism is needed.
// Deliberately feeds PO scoring ONLY: doiRung() keeps measuring cover at the truck's real ETA, so
// commitments look far ahead while safety stock stays near-term.
function poHorizonYMD(sku, day) {
  const reach = daysUntilResupplyCouldLand(sku, day);
  const days = Math.max(SCORING_HORIZON_DAYS, isFinite(reach) ? reach : Infinity);
  const cand = isFinite(days) ? addDaysYMD(day, days) : SIM_BUFFER_END;
  return cand > SIM_BUFFER_END ? SIM_BUFFER_END : cand;
}

const inTransitArrivals = computeInTransitArrivals(dispatchSheet.intransit, dispatchRecords);

// arrivalsByDay: ymd -> wh -> sku -> qty (mutable — seeded with in-transit, augmented by new dispatch decisions)
const arrivalsByDay = {};
for (const [ymd, whMap] of Object.entries(inTransitArrivals)) {
  arrivalsByDay[ymd] = arrivalsByDay[ymd] || {};
  for (const [wh, skuMap] of Object.entries(whMap)) {
    arrivalsByDay[ymd][wh] = arrivalsByDay[ymd][wh] || {};
    for (const [sku, qty] of Object.entries(skuMap)) arrivalsByDay[ymd][wh][sku] = (arrivalsByDay[ymd][wh][sku] || 0) + qty;
  }
}
function addArrival(ymd, wh, sku, qty) {
  arrivalsByDay[ymd] = arrivalsByDay[ymd] || {};
  arrivalsByDay[ymd][wh] = arrivalsByDay[ymd][wh] || {};
  arrivalsByDay[ymd][wh][sku] = (arrivalsByDay[ymd][wh][sku] || 0) + qty;
}
function getArrival(ymd, wh, sku) { return ((arrivalsByDay[ymd] || {})[wh] || {})[sku] || 0; }
function getPoDemand(ymd, wh, sku) { return ((poDemandByDay[ymd] || {})[wh] || {})[sku] || 0; }

const extraInTransitLog = [];
if (EXTRA_IN_TRANSIT) {
  for (const entry of EXTRA_IN_TRANSIT.split(';').map(s => s.trim()).filter(Boolean)) {
    const parts = entry.split(':');
    if (parts.length !== 4) { console.error(`--extra-in-transit: bad entry "${entry}", expected FACILITY:WAREHOUSE:SKU:QTY`); process.exit(1); }
    const [facility, wh, sku, qtyStr] = parts;
    const qty = Number(qtyStr);
    if (!TAT[facility] || TAT[facility][wh] === undefined) { console.error(`--extra-in-transit: unknown facility/warehouse "${facility}/${wh}"`); process.exit(1); }
    if (!SKUS.includes(sku)) { console.error(`--extra-in-transit: unknown SKU "${sku}"`); process.exit(1); }
    if (!Number.isFinite(qty) || qty <= 0) { console.error(`--extra-in-transit: bad quantity in "${entry}"`); process.exit(1); }
    const arrivalSerial = Math.max(todaySerial + TAT[facility][wh], ymdToSerial(WINDOW_START));
    const ymd = serialToYMD(arrivalSerial);
    addArrival(ymd, wh, sku, qty);
    extraInTransitLog.push({ facility, wh, sku, qty, ymd });
  }
}

// Running balance = projected on-hand at the START of the given simulated day
const balance = {};
for (const wh of WH_ORDER) { balance[wh] = {}; for (const sku of SKUS) balance[wh][sku] = (dispatchSheet.inventoryView[wh] || {})[sku] || 0; }

// Opening stock position, snapshotted BEFORE the simulation mutates `balance` — the DOI COVERAGE
// report needs the starting point to show what the ladder actually moved.
const openingBalance = {};
for (const wh of WH_ORDER) { openingBalance[wh] = {}; for (const sku of SKUS) openingBalance[wh][sku] = balance[wh][sku]; }

// ── Shortfall tracking (replaces the old "worst balance" model) ──────────────────────────
// Stock is FLOORED AT ZERO. Physically, demand that cannot be served is lost — it does not create
// negative inventory. Letting balances run negative (as this did) meant the deficit persisted
// forever and leaked into poNeed, pileGapForSku and doiRung, so the planner mis-measured its own
// world by ~1,840 units. What matters is not "how negative did it go" but "what could we not
// serve", which is what these track.
const unmetPO = {}, unmetDrr = {}, daysAtZero = {}, servedPO = {};
for (const wh of WH_ORDER) {
  unmetPO[wh] = {}; unmetDrr[wh] = {}; daysAtZero[wh] = {}; servedPO[wh] = {};
  for (const sku of SKUS) { unmetPO[wh][sku] = 0; unmetDrr[wh][sku] = 0; daysAtZero[wh][sku] = 0; servedPO[wh][sku] = 0; }
}
const missedPOEvents = []; // { day, wh, sku, ordered, served, short }
// Per-PO view of the same shortfalls, for the portal's "POs at risk" table. Kept SEPARATE from
// missedPOEvents rather than replacing it: that array's length is printed as a headline and parsed
// back out of the child process's stdout by the production-suggestion engine, so changing one
// bucket into several PO rows would silently change what that number means.
const missedPORows = []; // { day, wh, sku, poNumber, soNumber, channel, ordered, served, short, status }

// The single forward-step used everywhere: arrivals in, then PO demand, then ambient DRR, with a
// hard floor at zero. Previously the timeline was walked in three places with subtly different
// rules, so the simulation could disagree with itself; every caller now shares this.
// `record` is false for speculative look-ahead walks (scoring), true only for the real advance.
function stepDay(bal, wh, sku, ymd, record) {
  let b = bal + getArrival(ymd, wh, sku);
  const po = getPoDemand(ymd, wh, sku);
  let poShort = 0;
  if (po > 0) {
    const served = Math.min(po, Math.max(0, b));
    poShort = po - served;
    b -= served;
    if (record) {
      servedPO[wh][sku] += served;
      // The TOTAL must always satisfy short = ordered - served, so accumulate every shortfall here,
      // including sub-unit ones. Gating this used to drop fractional shortfalls from the per-SKU
      // column while they still reduced `served`, so the published table read
      // "9500 ordered, 7335 served, 2163 short" and the TOTAL row disagreed with the sum of its
      // own rows by 2 units.
      unmetPO[wh][sku] += poShort;
      // Threshold at half a unit, not epsilon: sub-unit rounding noise from fractional DRR is not a
      // missed order and must not be LISTED as one. Reporting only — never the arithmetic.
      if (poShort > 0.5) {
        missedPOEvents.push({ day: ymd, wh, sku, ordered: po, served, short: poShort });
        // Attribute the bucket's shortfall back to the individual orders inside it. The simulation
        // serves (day, warehouse, SKU) as one number, so which PO "missed" is an interpretation,
        // not a fact the engine computes: we fill in sheet order, first-come-first-served, which
        // is how parse_po_fulfillment.js walks its rows and how orders are actually worked through.
        //
        // Gating stays at BUCKET level (the `poShort > 0.5` above). Re-gating each apportioned row
        // would push more units under the threshold and quietly shrink the reconciliation total.
        // Nothing here mutates poRowsByDay -- stepDay is called speculatively with record=false far
        // more often than it records, and a mutated list would corrupt every later scoring pass.
        let remaining = served;
        for (const entry of (((poRowsByDay[ymd] || {})[wh] || {})[sku] || [])) {
          const rowServed = Math.min(entry.qty, Math.max(0, remaining));
          remaining -= rowServed;
          const rowShort = entry.qty - rowServed;
          if (rowShort <= 0) continue;
          missedPORows.push({
            day: ymd, wh, sku,
            poNumber: entry.poNumber, soNumber: entry.soNumber, channel: entry.channel,
            ordered: entry.qty, served: rowServed, short: rowShort,
            status: rowServed > 0 ? 'PARTIAL' : 'RESCHEDULE',
          });
        }
      }
    }
  }
  const drr = effDrr[wh][sku];
  const drrServed = Math.min(drr, Math.max(0, b));
  b -= drrServed;
  if (record) {
    unmetDrr[wh][sku] += drr - drrServed;
    if (b <= 0.0001) daysAtZero[wh][sku]++;
  }
  return { bal: b < 0 ? 0 : b, poShort };
}

// PO units this truck cannot serve, measured FROM ITS OWN ARRIVAL DAY forward.
// Scoring from today (the old behaviour) meant an order whose date had already passed kept
// generating tier-0 urgency every day until it fell out of the horizon — a truck that lands after
// the order date cannot serve it, so chasing it just burns capacity another warehouse needs.
// Observed before this fix: 1,260 units sent to Hyderabad across two consecutive trucks for a
// 30-unit order dated 31-Aug that neither truck (landing 02-Sep and 03-Sep) could ever reach.
//
// Returns summed PO shortfall rather than a worst-balance, which also retires the old
// `hasPO ? worst : Infinity` kludge: with no orders in range the sum is naturally zero, and a
// pre-existing deficit can no longer masquerade as PO need.
function poShortfallFromArrival(wh, sku, day, arrivalYMD, horizonYMD) {
  let bal = balance[wh][sku];
  // Walk silently up to the arrival day to establish the position the truck lands into.
  for (let d = day; d < arrivalYMD; d = addDaysYMD(d, 1)) bal = stepDay(bal, wh, sku, d, false).bal;
  let short = 0, firstShortDay = null;
  for (let d = arrivalYMD; d <= horizonYMD; d = addDaysYMD(d, 1)) {
    const r = stepDay(bal, wh, sku, d, false);
    bal = r.bal; short += r.poShort;
    if (r.poShort > 0.0001 && !firstShortDay) firstShortDay = d;
  }
  return { short, firstShortDay };
}

// (projectMinBalanceDrrOnly removed — the DRR-only "would ambient demand cause a stockout" walk was
// only ever consumed by the DRR_EMERGENCY_FLOOR_DAYS test, which the DOI ladder replaced. Ambient
// demand is now handled by doiRung(), which targets days of cover rather than a bare zero balance.)

const pool = { RONCH: {}, AMBER: {} };
const poolSince = { RONCH: {}, AMBER: {} }; // ymd this SKU's pool balance last went from 0 -> positive

// Each plant's pool OPENS at its current finished-goods stock, not at zero. FG is leftover stock
// from previous days; today's production is added on top by the main loop's D+1 rule (prodDay =
// day-1, and day 1 of the window is tomorrow) unless TODAY_PRODUCTION_ENABLED is false (see that
// flag's declaration), in which case today's row was already deleted from dailyProduction above —
// the two only avoid double-counting when the FG snapshot predates today's finished production.
// NOTE: no PRODUCTION_YIELD here. The 0.9 factor exists because *planned* output loses units to
// QC — FG is already-finished, physically counted stock, so discounting it would throw away real
// units. Yield stays on production only.
const fgParse = FG_ENABLED ? parseCurrentFG(wbWH) : { fg: { RONCH: {}, AMBER: {} }, hold: { RONCH: {}, AMBER: {} }, unresolved: [], found: false };
const currentFG = fgParse.fg;
const currentHold = fgParse.hold || { RONCH: {}, AMBER: {} };
// Hard stop rather than a warning. parseCurrentFG used to degrade quietly to empty pools, so a
// broken anchor produced a plausible-looking plan, exit code 0, and no hint that ~1,000 units of
// plant stock had been dropped -- which is exactly how it went unnoticed. If FG is meant to be
// read, failing to read it is a data problem to fix, not a run to keep going with. --no-fg is the
// deliberate way to plan without plant FG.
if (FG_ENABLED && !fgParse.found) {
  console.error('\n❌ Plant FG could not be read from Current Inventory (see the warning above).');
  console.error('   Refusing to plan with both plant pools at zero, which would silently ignore all');
  console.error('   finished goods at Ronch and Amber. Fix the sheet, or pass --no-fg deliberately.\n');
  process.exit(1);
}
// --include-hold: treat "<facility> Hold" stock as dispatchable from day one, alongside FG. Hold is
// physically present, already-finished stock that is normally blocked (QC, quarantine, allocation),
// so it is excluded by default. Use this only when the blocker has actually been cleared and the
// units can ship — it is the single largest lever available when a scarce SKU sits in Hold.
// Like FG, it takes no yield factor and is seeded at WINDOW_START.
for (const f of FACILITIES) for (const sku of SKUS) {
  pool[f][sku] = ((currentFG[f] || {})[sku] || 0) + (INCLUDE_HOLD ? ((currentHold[f] || {})[sku] || 0) : 0);
}
// Age tracking must know these SKUs are already populated, or ageDaysOf() reads them as never-
// filled. How long the FG actually sat is unknowable from the sheet (no timestamp), so start the
// clock at WINDOW_START — age 0, the conservative choice, and age is only the lowest-weight tiebreak.
for (const f of FACILITIES) for (const sku of SKUS) if (pool[f][sku] > 0) poolSince[f][sku] = WINDOW_START;

// ── Scarce vs surplus, decided once up front ─────────────────────────────────────────────
// Everything that could ever exist of this SKU across the window.
const supplyOf = sku => {
  const onHand = WH_ORDER.reduce((s, wh) => s + openingBalance[wh][sku], 0);
  const inTr = WH_ORDER.reduce((s, wh) => s + ((dispatchSheet.intransit[wh] || {})[sku] || 0), 0);
  // Must mirror the pool seeding above, or scarcity classification and the Production Mix gap
  // would price in stock the simulation is allowed to ship (or vice versa).
  const fg = FACILITIES.reduce((s, f) => s + ((currentFG[f] || {})[sku] || 0)
    + (INCLUDE_HOLD ? ((currentHold[f] || {})[sku] || 0) : 0), 0);
  let prod = 0;
  for (const ymd of prodDaysSorted) {
    if (!inProdWindow(ymd)) continue;
    for (const f of FACILITIES) prod += Math.round((((dailyProduction[ymd] || {})[f] || {})[sku] || 0) * PRODUCTION_YIELD);
  }
  return onHand + inTr + fg + prod;
};
const simDays = ymdToSerial(SIM_BUFFER_END) - ymdToSerial(WINDOW_START) + 1;
const demandOf = sku => {
  let po = 0;
  for (const whMap of Object.values(poDemandByDay)) for (const wh of WH_ORDER) po += ((whMap[wh] || {})[sku] || 0);
  return po + WH_ORDER.reduce((s, wh) => s + effDrr[wh][sku], 0) * simDays;
};
// A SKU is SCARCE when there is not enough of it, anywhere, to meet committed orders plus ambient
// demand. Scarce stock must never be spent as truck filler — it has to be held at the plant and
// sent later alongside a surplus SKU, still inside the 500-725 truck bounds. Before this rule the
// last-resort top-up grabbed whatever was left in the pool, so on 29-Aug Ronch shipped its ENTIRE
// 1,150-unit M1-2nd Gen pool on day one (688 to Kolkata, 462 to Mumbai) simply because it had only
// 150 units of anything else to pad with — starving Bangalore and Kolkata's later orders.
const skuIsScarce = {};
for (const sku of SKUS) skuIsScarce[sku] = demandOf(sku) > supplyOf(sku);

// Everything still scheduled to land at this warehouse from `fromYMD` onward — i.e. stock already
// committed to it but not yet arrived. Arrivals for `fromYMD` itself count, because the day's
// balance advance runs after dispatch decisions are made.
function futureArrivals(wh, sku, fromYMD) {
  let s = 0;
  for (const [ymd, whMap] of Object.entries(arrivalsByDay)) {
    if (ymd >= fromYMD) s += ((whMap[wh] || {})[sku] || 0);
  }
  return s;
}
// How far below its target network share (WH_SPLIT) this warehouse sits for this SKU — 0 if at or
// above target.
//
// Measured on PIPELINE position (balance + everything already on the road), not the raw current
// balance. Using the bare balance made a warehouse look empty for the whole length of its lane while
// trucks were en route, so the planner kept re-sending: Kolkata, the longest lane at 7 days from
// Ronch, absorbed 35.8% of all M3 Pro against a 10% target because up to a week of dispatches were
// invisible to this calculation. The DOI tiers avoid this via positionAtArrival(); this is the same
// fix for the fallback tier.
function pileGapForSku(wh, sku, day) {
  const posOf = w => Math.max(0, balance[w][sku] + futureArrivals(w, sku, day));
  const networkTotal = WH_ORDER.reduce((s, w) => s + posOf(w), 0);
  return Math.max(0, WH_SPLIT[wh] * networkTotal - (balance[wh][sku] + futureArrivals(wh, sku, day)));
}
// Days this facility's pool for this SKU has sat continuously non-zero.
function ageDaysOf(f, sku, day) {
  return poolSince[f][sku] ? (ymdToSerial(day) - ymdToSerial(poolSince[f][sku])) : 0;
}
// Projected stock position for this warehouse×SKU on the day a truck leaving now would LAND:
// current balance, plus everything already scheduled to arrive by then, minus DRR and PO consumed
// in the meantime. Using the arrival date (not today) is what stops the planner re-sending to a
// warehouse that already has trucks on the road — the old pileGapForSku() read the raw current
// balance and ignored in-flight stock entirely, so it over-sent to the same destination.
// Uses the shared stepDay() so it floors at zero exactly like the real advance does — a warehouse
// that ran dry is at 0, not deeply negative, so DOI and pile-gap read a physical position.
function positionAtArrival(wh, sku, day, arrivalYMD) {
  let bal = balance[wh][sku];
  for (let d = day; d <= arrivalYMD; d = addDaysYMD(d, 1)) bal = stepDay(bal, wh, sku, d, false).bal;
  return bal;
}
// Which rung of DOI_LADDER this warehouse×SKU currently sits below, and how many units it needs to
// reach the top of that rung. Tier 1..N map to the ladder; tier LADDER_FALLBACK_TIER means it is
// past the top rung (or has no ambient DRR at all, so DOI is meaningless) and falls through to the
// WH_SPLIT pile-gap. Deliberately fills only to the TOP OF THE CURRENT RUNG, never straight to the
// ceiling — a warehouse at 3 DOI gets enough for 7, then re-enters the ladder for the next rung on
// a later truck. That is the mechanism that stocks everyone gradually instead of dumping on one.
const LADDER_FALLBACK_TIER = DOI_LADDER.length + 1;
function doiRung(wh, sku, day, arrivalYMD) {
  const drr = effDrr[wh][sku];
  if (!DOI_ENABLED || !(drr > 0)) return { tier: LADDER_FALLBACK_TIER, need: 0, doi: Infinity };
  const pos = positionAtArrival(wh, sku, day, arrivalYMD);
  const doi = pos / drr;
  for (let i = 0; i < DOI_LADDER.length; i++) {
    if (doi < DOI_LADDER[i]) return { tier: i + 1, need: Math.max(0, DOI_LADDER[i] * drr - pos), doi };
  }
  return { tier: LADDER_FALLBACK_TIER, need: 0, doi };
}

// Priority for "how much does sending SKU `sku` to warehouse `wh` matter right now", as an explicit
// (tier, magnitude) pair compared lexicographically — tier ascending, magnitude descending.
//
// Tiers: 0 = a committed PO would go unfulfilled (no floor, always first)
//        1..N = below DOI_LADDER rung N (1 = below 7 DOI, the hard floor)
//        N+1 = past the top rung / no DRR -> WH_SPLIT pile-gap
//        N+2 = nothing needed anywhere; age-at-plant is the only tiebreak
// This replaced magnitude-stacked scoring (poNeed*1e9 + ... + pileGap*1e3). With six tiers and
// deficits in the hundreds, magnitude packing bleeds between bands — a pileGap sum over 1,000 units
// already collided with the tier above it — and scaling the multipliers up would run past 2^53.
const NOTHING_TIER = LADDER_FALLBACK_TIER + 1;
// poHorizonYMD walks the production calendar, and this is on the hottest path in the simulation —
// memoise per (sku, day), which is all it depends on.
const poHorizonCache = new Map();
function poHorizonFor(sku, day) {
  const k = sku + '|' + day;
  let v = poHorizonCache.get(k);
  if (v === undefined) { v = poHorizonYMD(sku, day); poHorizonCache.set(k, v); }
  return v;
}
function skuScoreParts(f, wh, sku, day, arrivalYMD) {
  // PO urgency uses the production-aware horizon (extends once this SKU has no resupply coming);
  // every other tier below is measured at the truck's real ETA.
  const horizonYMD = poHorizonFor(sku, day);
  // Measured from arrivalYMD, so only orders this truck could actually serve count.
  const full = poShortfallFromArrival(wh, sku, day, arrivalYMD, horizonYMD);
  const poNeed = full.short;
  if (poNeed > 0) {
    // MEMBERSHIP is decided on the full horizon above (poNeed) — that is what protects against a
    // production cliff. RANKING uses a narrower figure: only the shortfall this truck alone can
    // save, i.e. before a following truck could land. Ranking on the full horizon sent scarce
    // stock to whichever warehouse had the biggest hole, which is often the one that cannot be
    // fixed at all. Observed 29-Aug: Mumbai showed 1,760 units of M1-2nd Gen orders stacked to
    // 12-Sep and won 725 units it needed 52 of, while Kolkata's smaller but UNRECOVERABLE 362
    // got nothing and its 05-Sep order missed by 395.
    const nextResupply = nextResupplyArrivalYMD(wh, sku, day);
    // Shortfall at or after the next resupply is that truck's job, not this one's. With no
    // resupply possible the window widens to the full horizon — exactly the cliff case.
    let urgentEnd = horizonYMD;
    if (nextResupply) {
      const cap = addDaysYMD(nextResupply, -1);
      if (cap < urgentEnd) urgentEnd = cap;
    }
    if (urgentEnd < arrivalYMD) urgentEnd = arrivalYMD; // never shorter than the arrival day itself
    const urgentNeed = poShortfallFromArrival(wh, sku, day, arrivalYMD, urgentEnd).short;
    // When urgent need ties at zero — which happens whenever a following truck could in principle
    // reach every candidate — break the tie on EARLIEST DEADLINE, not on hole size. Falling back to
    // magnitude reintroduced the original bug: on 29-Aug both Bangalore and Mumbai showed zero
    // urgent need, Mumbai won on its 1,760-unit horizon total, and the "following truck" that was
    // assumed to rescue Bangalore never came — it got no M1-2nd Gen until 12-Sep and missed 05-Sep.
    return {
      tier: 0, need: urgentNeed, fallbackNeed: poNeed,
      deadline: full.firstShortDay, doi: doiRung(wh, sku, day, arrivalYMD).doi,
    };
  }
  const rung = doiRung(wh, sku, day, arrivalYMD);
  if (rung.tier <= DOI_LADDER.length && rung.need > 0) return { tier: rung.tier, need: rung.need, doi: rung.doi };
  const pileGap = pileGapForSku(wh, sku, day);
  if (pileGap > 0) return { tier: LADDER_FALLBACK_TIER, need: pileGap, doi: rung.doi };
  return { tier: NOTHING_TIER, need: 0, doi: rung.doi };
}
// The lane factor scales the MAGNITUDE only, never the tier — so a slow lane can reorder warehouses
// within a rung, but can never demote a real PO shortfall or a below-7-DOI warehouse beneath a
// comfortable one. Same guarantee the previous magnitude-based scoring gave.
function skuScoreKey(f, wh, sku, day, arrivalYMD) {
  const { tier, need, fallbackNeed, deadline } = skuScoreParts(f, wh, sku, day, arrivalYMD);
  const lf = laneFactor(f, wh);
  return { tier, mag: need * lf, fallbackMag: (fallbackNeed || 0) * lf, deadline };
}
// Lexicographic, in order:
//   1. lower tier wins
//   2. larger lane-adjusted URGENT need — the shortfall only this truck can save
//   3. EARLIEST DEADLINE — the soonest order that would go unserved. This is what breaks the very
//      common tie where every candidate's urgent need is zero. Ranking on hole size there is what
//      caused the original defect, sending stock to whichever warehouse had the biggest total
//      shortfall rather than the one whose order is about to be missed.
//   4. only then, overall shortfall
function cmpKey(a, b) {
  if (a.tier !== b.tier) return a.tier - b.tier;
  if (Math.abs(a.mag - b.mag) > 1e-9) return b.mag - a.mag;
  if (a.deadline !== b.deadline) {
    if (!a.deadline) return 1;          // no deadline at all ranks last
    if (!b.deadline) return -1;
    return a.deadline < b.deadline ? -1 : 1;
  }
  return (b.fallbackMag || 0) - (a.fallbackMag || 0);
}

// Optional day-by-day trace for one warehouse x SKU, e.g. --trace Mumbai,"M2 Pro"
const traceArg = flagValue('--trace');
const traceTarget = traceArg ? { wh: traceArg.split(',')[0], sku: normalizeSku(traceArg.split(',')[1]) || traceArg.split(',')[1] } : null;
const traceLog = [];

const dispatchPlan = []; // { date, facility, wh, skus: {sku:qty}, total, eta }
const agedClearances = []; // { date, facility, sku, qty, ageDays } — informational log only, not a forced override

// Tracks the most recent PURE pile-up destination (no PO/DRR need anywhere) — GLOBAL across both
// facilities, not per-facility, since two independent facility loops choosing the same warehouse on
// the same day is exactly the "simultaneous huge inward" problem this exists to prevent. Only tier-3
// (pile-gap-only) picks update this; a genuine PO/DRR emergency is never suppressed by it.
let lastPileUpWh = null;

let day = WINDOW_START;
while (day <= WINDOW_END) {
  const prodDay = addDaysYMD(day, -1);
  for (const f of FACILITIES) {
    const raw = (dailyProduction[prodDay] || {})[f] || {};
    for (const sku of SKUS) {
      const added = Math.round((raw[sku] || 0) * PRODUCTION_YIELD);
      const wasEmpty = pool[f][sku] <= 0;
      pool[f][sku] += added;
      if (wasEmpty && pool[f][sku] > 0) poolSince[f][sku] = day;
    }
  }

  {
    let guard = 0;
    const poolTotal = f => Object.values(pool[f]).reduce((a, b) => a + b, 0);
    // Facilities that tried to build a truck today and could not reach the 500 floor without
    // spending scarce stock as filler. They hold and wait for a surplus SKU to ride along with;
    // recorded so the loop below does not retry the same impossible build. Cleared each day.
    const heldToday = new Set();
    // Hard floor: a truck never departs below 500 units, full stop — even same-day overflow from a
    // single oversized production batch waits in the pool rather than going out under-filled. Age
    // at plant only ever influences WHICH SKUs/destination get picked once this floor is crossed
    // (see skuScoreParts) — it never forces a sub-500 dispatch on its own.
    //
    // BOTH PLANTS ARE CONSIDERED TOGETHER. This used to be two sequential loops (all of Ronch's
    // trucks, then all of Amber's), which handed Ronch first pick of destination every single day —
    // arbitrary, and neither plant could see what the other was about to do. Now every
    // (facility, warehouse) pair competes on the same ladder, so a destination goes to whichever
    // plant serves it best. Applied at EVERY tier, PO emergencies included: that strengthens safety
    // rather than weakening it, since an emergency gets whichever plant reaches it soonest.
    while (FACILITIES.some(f => poolTotal(f) >= TRUCK_MIN && !heldToday.has(f)) && guard++ < 40) {
      // Rank (facility, warehouse) pairs lexicographically: the BEST (lowest) tier any pool SKU
      // reaches at that warehouse, then the lane-adjusted sum of needs at that tier. Taking the best
      // tier — not a sum across tiers — is what enforces the ladder globally: no warehouse can be
      // pushed toward 15 DOI while another still sits below 7, because tier 1 beats tier 2 outright.
      // DOI is measured at each pair's real ETA; PO urgency uses the production-aware horizon.
      const candidates = [];
      // Under --no-joint only the first plant that can still fill a truck is considered, which
      // reproduces the old sequential Ronch-then-Amber ordering.
      const eligible = FACILITIES.filter(f => poolTotal(f) >= TRUCK_MIN && !heldToday.has(f));
      for (const f of (JOINT_PLANTS ? eligible : eligible.slice(0, 1))) {
        for (const wh of WH_ORDER) {
          const eta = addDaysYMD(day, TAT[f][wh]);
          const lf = laneFactor(f, wh);
          let bestTier = Infinity, mag = 0, fallbackMag = 0, deadline = null, relevant = false;
          for (const sku of SKUS) {
            if (pool[f][sku] <= 0) continue;
            relevant = true;
            const p = skuScoreParts(f, wh, sku, day, eta);
            if (p.tier < bestTier) { bestTier = p.tier; mag = p.need * lf; fallbackMag = (p.fallbackNeed || 0) * lf; deadline = p.deadline || null; }
            else if (p.tier === bestTier) {
              mag += p.need * lf; fallbackMag += (p.fallbackNeed || 0) * lf;
              // the warehouse's deadline is the soonest across the SKUs this truck could carry
              if (p.deadline && (!deadline || p.deadline < deadline)) deadline = p.deadline;
            }
          }
          if (relevant) candidates.push({ f, wh, tier: bestTier, mag, fallbackMag, deadline });
        }
      }
      if (!candidates.length) break;
      candidates.sort(cmpKey);

      // EMERGENCY = tier 0 (a committed PO would go unfulfilled) or tier 1 (below the 7-DOI hard
      // floor). Everything above that is ordinary laddering and counts as PILE-UP, so the rotation
      // rule still applies: a PILE-UP pick never lands on the same warehouse twice in a row, tracked
      // globally across both facilities. Since tiers are strictly ordered, rolling to the next-best
      // candidate can never skip past a genuine emergency.
      const isEmergencyTier = t => t <= 1;
      let winnerIdx = 0;
      if (!isEmergencyTier(candidates[0].tier) && candidates[0].wh === lastPileUpWh && candidates.length > 1) {
        winnerIdx = 1;
      }
      const f = candidates[winnerIdx].f;
      const best = candidates[winnerIdx].wh;
      const isPileUp = !isEmergencyTier(candidates[winnerIdx].tier);
      if (isPileUp) lastPileUpWh = best;

      const arrivalYMD = addDaysYMD(day, TAT[f][best]);
      const truck = {}; let total = 0;
      const pickReason = isPileUp ? 'PILE-UP' : 'EMERGENCY';
      // Carried into the JSON so the portal can colour-code the actual ladder rung. EMERGENCY/
      // PILE-UP flattens seven tiers into two words: tier 0 (a committed PO will go unserved) and
      // tier 1 (below the 7-day DOI rung) both read "EMERGENCY", but they are very different
      // degrees of trouble and the tab can show that.
      const pickTier = candidates[winnerIdx].tier;

      // Fill in tier order. Each SKU is topped up only to the ceiling of the rung it is CURRENTLY in
      // — a warehouse at 3 DOI gets enough to reach 7, not 45 — so the truck spreads across whatever
      // is most deprived instead of satisfying one SKU all the way up.
      const rank = () => SKUS.filter(s => pool[f][s] > 0)
        .map(sku => ({ sku, ...skuScoreKey(f, best, sku, day, arrivalYMD) }))
        .sort(cmpKey);

      const takeFor = sku => {
        const { tier, need, fallbackNeed } = skuScoreParts(f, best, sku, day, arrivalYMD);
        // For a SURPLUS SKU, fill to the destination's full-horizon demand — there is plenty, and a
        // bigger drop now saves a truck later. For a SCARCE SKU, fill only to the URGENT need (what
        // this truck alone can save): taking its full-horizon need would hand the entire pool to
        // whichever warehouse ranked first, which is precisely the over-allocation being fixed.
        // Observed before this: Mumbai took 622 of Ronch's 1,150 M1-2nd Gen as pass-1 "need", so the
        // whole pool still left on day one even after filler was restricted.
        const fillNeed = tier === 0
          ? (skuIsScarce[sku] ? need : Math.max(need, fallbackNeed || 0))
          : need;
        if (tier >= NOTHING_TIER || fillNeed <= 0) return 0;
        return Math.round(Math.min(pool[f][sku], Math.round(fillNeed), TRUCK_TARGET - total));
      };
      // Re-rank between picks: taking stock for one SKU changes its rung, so the next pick should go
      // to whatever is now most deprived rather than following a stale ordering.
      for (let pass = 0; pass < 24 && total < TRUCK_TARGET; pass++) {
        const scored = rank();
        if (!scored.length) break;
        let moved = false;
        for (const { sku } of scored) {
          if (total >= TRUCK_TARGET) break;
          const take = takeFor(sku);
          if (take > 0) {
            truck[sku] = (truck[sku] || 0) + take; total += take; pool[f][sku] -= take;
            addArrival(arrivalYMD, best, sku, take); // so the next rung calc sees what we just loaded
            moved = true;
            break;
          }
        }
        if (!moved) break;
      }
      // Top-up to clear the 500-unit floor — but ONLY from surplus SKUs. A scarce SKU is never
      // spent as filler: it is held at the plant and sent later alongside a surplus SKU, still
      // inside the same 500-725 bounds. See skuIsScarce for why.
      if (total < TRUCK_TARGET) {
        for (const { sku } of rank()) {
          if (total >= TRUCK_TARGET) break;
          if (skuIsScarce[sku]) continue;
          const take = Math.round(Math.min(pool[f][sku], TRUCK_TARGET - total));
          if (take > 0) {
            truck[sku] = (truck[sku] || 0) + take; total += take; pool[f][sku] -= take;
            addArrival(arrivalYMD, best, sku, take);
          }
        }
      }
      // Still short of the floor with only scarce stock left to pad with? Hold it. Undo whatever
      // this attempt loaded, mark the facility done for today so the outer loop does not retry the
      // same impossible build, and let the pool carry forward to a day with surplus stock to ride
      // along with.
      if (total < TRUCK_MIN) {
        for (const [sku, qty] of Object.entries(truck)) {
          pool[f][sku] += qty;
          addArrival(arrivalYMD, best, sku, -qty); // reverse the provisional arrival
        }
        heldToday.add(f);
        continue;
      }
      if (total <= 0) break;

      // Informational only now (not a forced override) — log any shipped SKU that had sat >=
      // MAX_HOLD_DAYS, for visibility into how much the age tiebreak is actually doing.
      for (const [sku, qty] of Object.entries(truck)) {
        const age = ageDaysOf(f, sku, day);
        if (age >= MAX_HOLD_DAYS) agedClearances.push({ date: day, facility: f, sku, qty, ageDays: age });
      }

      // Age resets whenever a SKU is actually shipped, not only when its pool fully empties —
      // a balance that's actively being drawn down every cycle isn't "stale" just because it
      // never quite hits zero (e.g. Amber's ~765/day output vs a 725 truck cap always leaves a
      // small, but freshly-churning, remainder).
      for (const sku of Object.keys(truck)) poolSince[f][sku] = pool[f][sku] > 0 ? day : null;
      // NOTE: arrivals are registered inside the fill loop above, as each SKU is loaded, so that the
      // rung calculation for the next pick already accounts for what this truck is carrying. Do NOT
      // re-register them here — that would double-count every unit into the destination's balance.
      dispatchPlan.push({ date: day, facility: f, wh: best, skus: truck, total, eta: arrivalYMD, reason: pickReason, tier: pickTier });
    }
  }

  // Advance balance forward by one day. record=true: this is the real advance, so unmet PO/DRR and
  // days-at-zero are captured here and nowhere else.
  for (const wh of WH_ORDER) for (const sku of SKUS) {
    const arr = getArrival(day, wh, sku), drr = effDrr[wh][sku], po = getPoDemand(day, wh, sku);
    const opening = balance[wh][sku];
    balance[wh][sku] = stepDay(balance[wh][sku], wh, sku, day, true).bal;
    if (traceTarget && wh === traceTarget.wh && sku === traceTarget.sku) {
      traceLog.push({ day, opening, arrival: arr, drr, po, closing: balance[wh][sku] });
    }
  }
  day = addDaysYMD(day, 1);
}

// Continue advancing balance (no more new dispatch decisions) through to PO_CUTOFF, so trucks that
// left on the final window day still have their arrivals judged against real orders. Shortfall
// tracking carries straight on from the active window — no reset.
let simDay = addDaysYMD(WINDOW_END, 1);
while (simDay <= SIM_BUFFER_END) {
  for (const wh of WH_ORDER) for (const sku of SKUS) {
    const arr = getArrival(simDay, wh, sku), drr = effDrr[wh][sku], po = getPoDemand(simDay, wh, sku);
    const opening = balance[wh][sku];
    balance[wh][sku] = stepDay(balance[wh][sku], wh, sku, simDay, true).bal;
    if (traceTarget && wh === traceTarget.wh && sku === traceTarget.sku) {
      traceLog.push({ day: simDay, opening, arrival: arr, drr, po, closing: balance[wh][sku] });
    }
  }
  simDay = addDaysYMD(simDay, 1);
}

// ═══════════════════════════════════════════════════════════════════════
// Output
// ═══════════════════════════════════════════════════════════════════════
console.log('\n' + '═'.repeat(100));
console.log(`  🚚 FIRST-MILE DISPATCH PLAN — ${ymdToLabel(WINDOW_START)} to ${ymdToLabel(WINDOW_END)}  (generated ${nowIST})`);
console.log('═'.repeat(100));

console.log(TODAY_PRODUCTION_EXCLUDED_FACILITIES.length === 0
  ? `\n  Today's production (${ymdToLabel(todayYMD)}) INCLUDED at both facilities — assumed not yet reflected in the FG snapshot below.`
  : TODAY_PRODUCTION_EXCLUDED_FACILITIES.length === FACILITIES.length
    ? `\n  Today's production (${ymdToLabel(todayYMD)}) EXCLUDED at both facilities — assumed already folded into the FG snapshot below.`
    : `\n  Today's production (${ymdToLabel(todayYMD)}) EXCLUDED for ${TODAY_PRODUCTION_EXCLUDED_FACILITIES.join(', ')} (already folded into its FG snapshot) and INCLUDED for ${FACILITIES.filter(f => !TODAY_PRODUCTION_EXCLUDED_FACILITIES.includes(f)).join(', ')} (not yet reflected in its FG snapshot).`);

if (extraInTransitLog.length) {
  console.log('\n⚠️  EXTRA IN-TRANSIT ASSUMED FOR THIS RUN ONLY (--extra-in-transit, not from any sheet):');
  for (const e of extraInTransitLog) {
    console.log(`     ${e.facility} → ${e.wh}   ${e.sku}: ${fmt0(e.qty)} units   arriving ${ymdToLabel(e.ymd)} (today + ${TAT[e.facility][e.wh]}d TAT)`);
  }
}

// Opening FG is printed on EVERY run, up front. The source cell carries no date stamp, so this
// line is the only thing standing between a value nobody refreshed and a plan that ships units
// which do not exist — eyeball it.
const fgTotalOf = f => SKUS.reduce((s, sku) => s + ((currentFG[f] || {})[sku] || 0), 0);
const holdTotalOf = f => SKUS.reduce((s, sku) => s + ((currentHold[f] || {})[sku] || 0), 0);
if (!FG_ENABLED) {
  console.log('\n⚠️  --no-fg: plant pools open EMPTY; the Current Inventory plant FG/Hold table is ignored.');
} else {
  console.log('\n┌─ OPENING FG AT PLANT  (WH-Channel-SKU → Current Inventory → plant FG/Hold table; FG rows only)\n');
  for (const f of FACILITIES) {
    const parts = SKUS.filter(s => (currentFG[f] || {})[s] > 0).map(s => `${s}:${fmt0(currentFG[f][s])}`);
    console.log(`  ${pad(f, 7)} ${padNum(fmt0(fgTotalOf(f)), 6)} units  ${parts.length ? '(' + parts.join(', ') + ')' : '(none)'}`);
  }
  console.log(`  ${pad('TOTAL', 7)} ${padNum(fmt0(FACILITIES.reduce((s, f) => s + fgTotalOf(f), 0)), 6)} units — added to the pool with NO yield factor (already-finished stock).`);
  // Hold is real stock that exists but is not dispatchable. Naming it keeps the exclusion visible
  // rather than letting that inventory silently disappear from the picture.
  const holdAll = FACILITIES.reduce((s, f) => s + holdTotalOf(f), 0);
  if (holdAll > 0) {
    const detail = FACILITIES.filter(f => holdTotalOf(f) > 0).map(f => {
      const parts = SKUS.filter(s => (currentHold[f] || {})[s] > 0).map(s => `${s}:${fmt0(currentHold[f][s])}`);
      return `${f} ${fmt0(holdTotalOf(f))} (${parts.join(', ')})`;
    }).join('; ');
    console.log(INCLUDE_HOLD
      ? `  ${pad('HOLD', 7)} ${padNum(fmt0(holdAll), 6)} units **INCLUDED** via --include-hold — treated as dispatchable from day one: ${detail}`
      : `  ${pad('HOLD', 7)} ${padNum(fmt0(holdAll), 6)} units EXCLUDED — not dispatchable: ${detail}`);
    if (INCLUDE_HOLD) {
      console.log('          ⚠️  This plan assumes the Hold blocker is cleared and these units can physically ship.');
      console.log('              If any part of Hold is still blocked, re-run without --include-hold.');
    }
  }
  if (fgParse.unresolved.length) {
    console.log('\n  ⚠️  UNRECOGNISED FG ENTRIES — these units were NOT counted, fix the sheet:');
    for (const u of fgParse.unresolved) console.log('       ' + u);
  }
}

console.log(`\nTotal dispatch events: ${dispatchPlan.length}\n`);

if (traceTarget) {
  console.log(`┌─ TRACE: ${traceTarget.wh} × ${traceTarget.sku}\n`);
  console.log('Date         │ Opening  │ Arrival │   DRR │ PO Demand │ Closing');
  console.log('─────────────┼──────────┼─────────┼───────┼───────────┼─────────');
  for (const t of traceLog) {
    console.log(`${pad(ymdToLabel(t.day), 12)} │ ${padNum(fmt1(t.opening), 8)} │ ${padNum(fmt1(t.arrival), 7)} │ ${padNum(fmt1(t.drr), 5)} │ ${padNum(fmt1(t.po), 9)} │ ${padNum(fmt1(t.closing), 7)}`);
  }
  console.log('');
}

const COLS = [12, 9, 11, 46, 8, 12, 10];
const HEADERS = ['Dispatch Date', 'Facility', '→ Warehouse', 'SKU Breakdown', 'Total', 'ETA', 'Reason'];
function pad(s, w) { s = String(s); return s.length >= w ? s.slice(0, w) : s.padEnd(w); }
function padNum(s, w) { s = String(s); return s.padStart(w); }

console.log(HEADERS.map((h, i) => pad(h, COLS[i])).join(' │ '));
console.log(COLS.map(w => '─'.repeat(w)).join('─┼─'));
for (const ev of dispatchPlan) {
  const skuStr = Object.entries(ev.skus).map(([s, q]) => `${s}:${fmt0(q)}`).join(', ');
  console.log([
    pad(ymdToLabel(ev.date), COLS[0]),
    pad(ev.facility, COLS[1]),
    pad(ev.wh, COLS[2]),
    pad(skuStr, COLS[3]),
    padNum(fmt0(ev.total), COLS[4]),
    pad(ymdToLabel(ev.eta), COLS[5]),
    pad(ev.reason, COLS[6]),
  ].join(' │ '));
}

// Age tiebreak log (informational — age is folded into skuScore at the lowest weight, not a forced
// override; this just shows where it was actually a factor)
console.log(`\n\n┌─ AGE AT SHIPMENT  (informational — SKU had sat >= ${MAX_HOLD_DAYS} days when this truck shipped it)\n`);
if (agedClearances.length) {
  for (const c of agedClearances) {
    console.log(`  ${ymdToLabel(c.date)}  ${pad(c.facility, 7)} ${pad(c.sku, 14)} ${padNum(fmt0(c.qty), 6)} units, sat ${c.ageDays} days`);
  }
} else {
  console.log('  None — no SKU ever sat unshipped for ' + MAX_HOLD_DAYS + '+ days in this run.');
}

// Facility utilization summary
console.log('\n\n┌─ FACILITY UTILIZATION (production window ' + ymdToLabel(WINDOW_START === addDaysYMD(WINDOW_START,0) ? addDaysYMD(WINDOW_START,-1) : WINDOW_START) + ' – ' + ymdToLabel(addDaysYMD(WINDOW_END, -1)) + ')\n');
for (const f of FACILITIES) {
  const dispatched = dispatchPlan.filter(e => e.facility === f).reduce((s, e) => s + e.total, 0);
  const leftover = Object.values(pool[f]).reduce((a, b) => a + b, 0);
  console.log(`${f}: dispatched ${fmt0(dispatched)} units across ${dispatchPlan.filter(e => e.facility === f).length} trucks — undispatched residual at window end: ${fmt0(leftover)} units ${leftover > 0 ? '(' + Object.entries(pool[f]).filter(([,q])=>q>0).map(([s,q])=>`${s}:${fmt0(q)}`).join(', ') + ')' : ''}`);
  // Supply identity — opening FG + yielded production must equal everything dispatched plus what
  // is left standing. Printed so the run reconciles on its face rather than needing a side check.
  console.log(`        supply = opening FG ${fmt0(fgTotalOf(f))} + production×${PRODUCTION_YIELD} ${fmt0(dispatched + leftover - fgTotalOf(f))} = ${fmt0(dispatched + leftover)} (dispatched ${fmt0(dispatched)} + residual ${fmt0(leftover)})`);
}

// ── Lane efficiency ──────────────────────────────────────────────────────────────────────
// A truck on the slower lane is only a routing CHOICE if the other plant makes that SKU at all
// (facilityMakes / isDualSourced, computed at the production parse). Single-source volume (M1 Pro is
// Amber-only, M1-2nd Gen and M3 Pro are Ronch-only) is forced, not a planning failure.
const laneRows = [];
let laneUnitDays = 0, laneFloorDays = 0, swappableSlow = 0, forcedSlow = 0, swappableExcessDays = 0;
for (const e of dispatchPlan) {
  const other = otherFacility(e.facility);
  const used = TAT[e.facility][e.wh], bestT = BEST_TAT[e.wh], excess = used - bestT;
  laneUnitDays += used * e.total; laneFloorDays += bestT * e.total;
  let swap = 0, forced = 0;
  if (excess > 0) {
    for (const [sku, qty] of Object.entries(e.skus)) (facilityMakes[other].has(sku) ? (swap += qty) : (forced += qty));
    swappableSlow += swap; forcedSlow += forced; swappableExcessDays += swap * excess;
    laneRows.push({ date: e.date, facility: e.facility, wh: e.wh, total: e.total, used, bestT, excess, swap, forced });
  }
}
console.log('\n\n┌─ LANE EFFICIENCY  (natural lane = fastest plant for that warehouse: ' +
  WH_ORDER.map(w => `${w} ${Object.keys(BEST_TAT).length ? FACILITIES.find(f => TAT[f][w] === BEST_TAT[w]) : ''} ${BEST_TAT[w]}d`).join(', ') + ')\n');
if (!laneRows.length) {
  console.log('  Every truck is on its warehouse\'s fastest lane.');
} else {
  console.log('  Date         │ From   │ → Warehouse │  Units │ Lane      │ Swappable │ Forced');
  console.log('  ─────────────┼────────┼─────────────┼────────┼───────────┼───────────┼────────');
  for (const r of laneRows) {
    console.log(`  ${pad(ymdToLabel(r.date), 12)} │ ${pad(r.facility, 6)} │ ${pad(r.wh, 11)} │ ${padNum(fmt0(r.total), 6)} │ ${pad(r.used + 'd vs ' + r.bestT + 'd', 9)} │ ${padNum(fmt0(r.swap), 9)} │ ${padNum(fmt0(r.forced), 6)}`);
  }
}
console.log('');
console.log(`  Transit in plan: ${fmt0(laneUnitDays)} unit-days  │  lane floor: ${fmt0(laneFloorDays)} unit-days  │  ${laneFloorDays ? (100 * (laneUnitDays - laneFloorDays) / laneFloorDays).toFixed(1) : '0.0'}% above floor`);
console.log(`  Volume on the slower lane: ${fmt0(swappableSlow)} units genuinely swappable (other plant makes that SKU), ${fmt0(forcedSlow)} units forced single-source.`);
console.log(`  Remaining recoverable if the swappable volume moved to its natural lane: ${fmt0(swappableExcessDays)} unit-days.`);
console.log(`  Lane weight in force: ${LANE_WEIGHT} per excess day (--lane-weight 0 disables the preference).`);

// Stockout risk — worst balance tracked across the ENTIRE simulation (active window + buffer), not
// just after WINDOW_END, so a mid-window dip that recovers by window-end still shows up.
// ── Production outlook ───────────────────────────────────────────────────────────────────
// Production is not continuous, and a SKU whose last run falls inside the window while committed
// orders keep arriving afterwards is a production-planning problem, not a dispatch one. The planner
// now accounts for this itself (poHorizonYMD extends once resupply has run out) — this block makes
// the same fact visible to whoever reads the plan.
console.log('\n\n┌─ PRODUCTION OUTLOOK  (last scheduled run per facility×SKU, and what is committed after it)\n');
console.log('  Facility  SKU             runs   last run        then    units made');
console.log('  ────────  ──────────────  ────  ────────────  ────────  ──────────');
// PROD_FIRST / PROD_LAST / inProdWindow are defined up with the window constants — the scoring path
// needs them too, so they cannot live down here.
const poAfter = (sku, fromYMD) => {
  let s = 0;
  for (const [ymd, whMap] of Object.entries(poDemandByDay)) {
    if (ymd <= fromYMD) continue;
    for (const wh of WH_ORDER) s += ((whMap[wh] || {})[sku] || 0);
  }
  return s;
};
const cliffs = [];
for (const sku of SKUS) {
  const lastBy = {}, unitsBy = {}, runsBy = {};
  for (const f of FACILITIES) {
    let last = null, units = 0, runs = 0;
    for (const ymd of prodDaysSorted) {
      if (!inProdWindow(ymd)) continue;
      const q = (((dailyProduction[ymd] || {})[f] || {})[sku] || 0);
      if (q > 0) { last = ymd; units += q; runs++; }
    }
    lastBy[f] = last; unitsBy[f] = units; runsBy[f] = runs;
  }
  for (const f of FACILITIES) {
    if (!runsBy[f]) continue;
    const silence = ymdToSerial(PROD_LAST) - ymdToSerial(lastBy[f]);
    console.log('  ' + pad(f, 10) + pad(sku, 16) + padNum(String(runsBy[f]), 4) + '  ' +
      pad(ymdToLabel(lastBy[f]), 14) + padNum(silence > 0 ? silence + 'd idle' : 'to end', 8) +
      padNum(fmt0(unitsBy[f]), 12));
  }
  const networkLast = [lastBy.RONCH, lastBy.AMBER].filter(Boolean).sort().pop() || null;
  const after = networkLast ? poAfter(sku, networkLast) : poAfter(sku, addDaysYMD(WINDOW_START, -1));
  // Only a real cliff if the orders land after the last run AND that run is inside the window —
  // a SKU whose production continues to the final day has no gap to warn about.
  if (after > 0 && (!networkLast || networkLast < PROD_LAST)) cliffs.push({ sku, last: networkLast, after });
}
console.log('\n  Network view — committed PO demand landing after the LAST run anywhere:\n');
if (!cliffs.length) {
  console.log('  None — every SKU has production scheduled beyond its remaining committed demand.');
} else {
  console.log('  SKU             last run anywhere   PO demand after it   note');
  for (const c of cliffs) {
    const note = c.last ? 'must ship from stock made on/before that date'
      : 'NO production at all this window — cannot be fixed by dispatch';
    console.log('  ' + pad(c.sku, 16) + pad(c.last ? ymdToLabel(c.last) : 'never', 20) + padNum(fmt0(c.after), 18) + '   ' + note);
  }
  console.log('\n  These are production-planning constraints. Dispatch can only decide where the existing');
  console.log('  units go — the PO horizon widens automatically for these SKUs so the orders stay visible.');
}

// ── Production mix ───────────────────────────────────────────────────────────────────────
// Reporting only — no behaviour change. Missed POs and dead freight are two symptoms of ONE cause:
// the plan makes too much of some SKUs and too little of others. That was previously only
// inferable by cross-reading PO FILL RATE, PRODUCTION OUTLOOK and DOI COVERAGE together, so it
// kept being mistaken for a routing fault. Put the whole balance in one table.
const mixRows = [];
for (const sku of SKUS) {
  let po = 0;
  for (const whMap of Object.values(poDemandByDay)) for (const wh of WH_ORDER) po += ((whMap[wh] || {})[sku] || 0);
  const ambient = WH_ORDER.reduce((s, wh) => s + effDrr[wh][sku], 0) * simDays;
  const rawByFac = {};
  for (const f of FACILITIES) {
    let r = 0;
    for (const ymd of prodDaysSorted) if (inProdWindow(ymd)) r += (((dailyProduction[ymd] || {})[f] || {})[sku] || 0);
    rawByFac[f] = r;
  }
  const rawTot = FACILITIES.reduce((s, f) => s + rawByFac[f], 0);
  if (po <= 0 && ambient <= 0.5 && rawTot <= 0) continue;
  mixRows.push({ sku, po, ambient, rawByFac, rawTot, supply: supplyOf(sku), demand: po + ambient });
}
console.log('\n\n┌─ PRODUCTION MIX  (all-source supply vs committed PO + ambient demand, ' + ymdToLabel(WINDOW_START) + '..' + ymdToLabel(SIM_BUFFER_END) + ')\n');
console.log('  SKU               PO    ambient     demand  |   raw RONCH   raw AMBER  |  all-source     gap');
console.log('  ──────────────  ──────  ────────  ─────────  |   ─────────   ─────────  |  ──────────  ──────');
for (const m of mixRows) {
  const gap = Math.round(m.supply - m.demand);
  console.log('  ' + pad(m.sku, 16) + padNum(fmt0(m.po), 6) + padNum(fmt0(m.ambient), 10) + padNum(fmt0(m.demand), 11) +
    '  | ' + padNum(fmt0(m.rawByFac.RONCH), 11) + padNum(fmt0(m.rawByFac.AMBER), 12) +
    '  | ' + padNum(fmt0(m.supply), 11) + padNum((gap >= 0 ? '+' : '') + fmt0(gap), 8) +
    (gap < 0 ? '  ← SHORT' : ''));
}
console.log('\n  "gap" is all-source supply (on-hand + in-transit + plant FG + production x' + PRODUCTION_YIELD + ') minus total demand.');
console.log('  A negative gap cannot be fixed by routing, only by making more. A large positive gap is stock');
console.log('  that must still be trucked and warehoused — see the closing DOI figures below.');

// ── DOI coverage ─────────────────────────────────────────────────────────────────────────
// Did the ladder actually fill evenly? Opening vs closing days-of-cover per WH×SKU, plus the rung
// each one reached. `balance` at this point is the end-of-simulation position.
const doiOf = (bal, wh, sku) => (effDrr[wh][sku] > 0 ? bal / effDrr[wh][sku] : Infinity);
const rungOf = doi => {
  if (!isFinite(doi)) return 'no DRR';
  for (let i = 0; i < DOI_LADDER.length; i++) if (doi < DOI_LADDER[i]) return '<' + DOI_LADDER[i];
  return '>=' + DOI_LADDER[DOI_LADDER.length - 1];
};
const fmtDoi = d => (isFinite(d) ? (d < 0 ? d.toFixed(1) : d.toFixed(1)) : '  —');
console.log('\n\n┌─ DOI COVERAGE  (ambient-DRR days of cover; ladder ' + DOI_LADDER.join('/') + (DOI_ENABLED ? '' : ' — DISABLED via --no-doi') + ')\n');
console.log('  Warehouse    SKU              DRR/day   open bal   open DOI   close bal  close DOI   rung');
console.log('  ───────────  ──────────────  ────────  ─────────  ─────────  ──────────  ─────────  ───────');
const belowFloor = [];
for (const wh of WH_ORDER) for (const sku of SKUS) {
  const drr = effDrr[wh][sku];
  if (!(drr > 0) && openingBalance[wh][sku] <= 0 && balance[wh][sku] <= 0) continue;
  const o = doiOf(openingBalance[wh][sku], wh, sku), c = doiOf(balance[wh][sku], wh, sku);
  if (drr > 0 && c < DOI_LADDER[0]) belowFloor.push({ wh, sku, doi: c });
  console.log('  ' + pad(wh, 13) + pad(sku, 16) + padNum(drr.toFixed(1), 8) + padNum(fmt0(openingBalance[wh][sku]), 11) +
    padNum(fmtDoi(o), 11) + padNum(fmt0(balance[wh][sku]), 12) + padNum(fmtDoi(c), 11) + '  ' + rungOf(c));
}
// The headline test of "fill everyone gradually": the WORST-covered warehouse for each SKU should
// rise. A rising minimum means the ladder spread stock rather than piling it into one destination.
console.log('\n  Weakest warehouse per SKU (this is what the ladder is meant to lift):\n');
console.log('  SKU              min open DOI   min close DOI   at');
for (const sku of SKUS) {
  const rows = WH_ORDER.filter(wh => effDrr[wh][sku] > 0);
  if (!rows.length) continue;
  let o = Infinity, c = Infinity, at = '';
  for (const wh of rows) {
    o = Math.min(o, doiOf(openingBalance[wh][sku], wh, sku));
    const cc = doiOf(balance[wh][sku], wh, sku);
    if (cc < c) { c = cc; at = wh; }
  }
  console.log('  ' + pad(sku, 16) + padNum(fmtDoi(o), 13) + padNum(fmtDoi(c), 15) + '   ' + at + (c >= o ? '' : '   ⚠️ fell'));
}
if (belowFloor.length) {
  console.log(`\n  ⚠️  Still below the ${DOI_LADDER[0]}-DOI floor at window end — check whether production exists for these at all:`);
  for (const b of belowFloor) console.log('       ' + pad(b.wh, 13) + pad(b.sku, 16) + fmtDoi(b.doi) + ' DOI');
} else {
  console.log(`\n  ✅ Every warehouse×SKU with ambient demand ends at or above the ${DOI_LADDER[0]}-DOI floor.`);
}

// ── PO fill rate ─────────────────────────────────────────────────────────────────────────
// Replaces the old "worst projected balance" report, which is meaningless now stock floors at
// zero. This is the number that actually matters, and the one that would have caught every defect
// this report exists because of — check it on every run.
const poOrdered = {}, poServed = {}, poShortBy = {};
let ordTot = 0, srvTot = 0;
for (const sku of SKUS) { poOrdered[sku] = 0; poServed[sku] = 0; poShortBy[sku] = 0; }
for (const [ymd, whMap] of Object.entries(poDemandByDay)) {
  for (const wh of WH_ORDER) for (const sku of SKUS) {
    const q = ((whMap[wh] || {})[sku] || 0);
    poOrdered[sku] += q; ordTot += q;
  }
}
for (const wh of WH_ORDER) for (const sku of SKUS) {
  poServed[sku] += servedPO[wh][sku]; srvTot += servedPO[wh][sku];
  poShortBy[sku] += unmetPO[wh][sku];
}
// supplyOf / demandOf / simDays are defined up with the scarcity classification, before the
// simulation — both consumers share one definition of "is there enough of this SKU".
console.log('\n\n┌─ PO FILL RATE  (committed orders ' + ymdToLabel(WINDOW_START) + ' through ' + ymdToLabel(PO_CUTOFF) + ')\n');
console.log('  SKU             ordered    served     short   fill%  | hard supply deficit | dispatch-fixable');
console.log('  ──────────────  ────────  ────────  ────────  ─────  | ─────────────────── | ────────────────');
let hardTot = 0, fixTot = 0;
const termTotals = { ordered: 0, served: 0, short: 0 };
for (const sku of SKUS) {
  if (poOrdered[sku] <= 0 && poShortBy[sku] <= 0) continue;
  const netDrr = WH_ORDER.reduce((s, wh) => s + effDrr[wh][sku], 0);
  const demand = poOrdered[sku] + netDrr * simDays;
  const hard = Math.max(0, Math.round(demand - supplyOf(sku)));
  // Same rounding discipline as the xlsx: derive from rounded components and total the rows.
  const servedR = Math.round(poServed[sku]);
  const short = poOrdered[sku] - servedR;
  termTotals.ordered += poOrdered[sku]; termTotals.served += servedR;
  termTotals.short += short;
  const fix = Math.max(0, short - hard);
  // This column explains the SHORT, so cap it at the short. The raw deficit can exceed it (the
  // planner sacrifices ambient DRR to protect committed POs), and printing the uncapped figure
  // per-SKU while the TOTAL row printed the capped sum made a per-SKU value exceed the total.
  const hardShown = Math.min(hard, short);
  hardTot += hardShown; fixTot += fix;
  const fill = poOrdered[sku] > 0 ? (100 * poServed[sku] / poOrdered[sku]) : 100;
  console.log('  ' + pad(sku, 16) + padNum(fmt0(poOrdered[sku]), 8) + padNum(fmt0(poServed[sku]), 10) +
    padNum(fmt0(short), 10) + padNum(fill.toFixed(1), 7) + '  |' + padNum(fmt0(hardShown), 20) + ' |' + padNum(fmt0(fix), 17));
}
console.log('  ' + pad('TOTAL', 16) + padNum(fmt0(termTotals.ordered), 8) + padNum(fmt0(termTotals.served), 10) +
  padNum(fmt0(termTotals.short), 10) + padNum((termTotals.ordered > 0 ? 100 * termTotals.served / termTotals.ordered : 100).toFixed(1), 7) +
  '  |' + padNum(fmt0(hardTot), 20) + ' |' + padNum(fmt0(fixTot), 17));
console.log('');
if (fixTot > 0) {
  console.log('  ⚠️  ' + fmt0(fixTot) + ' units short beyond what supply can explain — that portion is a DISPATCH problem.');
} else {
  console.log('  ✅ Every unit short is explained by supply. Nothing further is recoverable by routing.');
}
if (missedPOEvents.length) {
  console.log('\n  Missed orders (' + missedPOEvents.length + '):\n');
  console.log('  Date          Warehouse    SKU              ordered    served     short');
  for (const m of missedPOEvents) {
    console.log('  ' + pad(ymdToLabel(m.day), 14) + pad(m.wh, 13) + pad(m.sku, 16) +
      padNum(fmt0(m.ordered), 8) + padNum(fmt0(m.served), 10) + padNum(fmt0(m.short), 10));
  }
  // Tie the listed rows back to the headline number. Only shortfalls above half a unit are listed
  // as orders, so a small residual here is expected — but it must be stated, not left as an
  // unexplained gap between two published figures.
  // Round the two components BEFORE differencing them, so the residual stated here always equals
  // the difference a reader gets by subtracting the two printed figures.
  const listed = Math.round(missedPOEvents.reduce((s, m) => s + m.short, 0));
  const headline = termTotals.short;
  const resid = headline - listed;
  console.log('\n  Listed rows total ' + fmt0(listed) + ' units against a headline short of ' + fmt0(headline) +
    (resid === 0 ? ' — reconciled exactly.'
      : '; the remaining ' + fmt0(resid) + ' is sub-unit shortfall spread across orders too small to list individually.'));
} else {
  console.log('\n  🟢 Every committed order is served in full and on time.');
}
const zeroRows = [];
for (const wh of WH_ORDER) for (const sku of SKUS) if (daysAtZero[wh][sku] > 0) zeroRows.push({ wh, sku, d: daysAtZero[wh][sku], drr: unmetDrr[wh][sku] });
if (zeroRows.length) {
  console.log('\n  Days at zero stock (ambient demand lost while empty):\n');
  for (const z of zeroRows.sort((a, b) => b.d - a.d)) {
    console.log('    ' + pad(z.wh, 13) + pad(z.sku, 16) + padNum(String(z.d), 4) + ' days   ' + padNum(fmt0(z.drr), 7) + ' units of ambient demand lost');
  }
}

// ── Production change recommendations ────────────────────────────────────────────────────
// When the shortfall is supply rather than routing, dispatch can do nothing — but production can.
// Suggestions are made RUN-WISE (a run = consecutive days of one SKU, which is what a line actually
// switches between) and only for runs far enough out to still be changeable.
const prodSuggestions = [];
if (SUGGEST_ENABLED) {
  // Capability comes from the WHOLE sheet, not just this window: a facility that made a SKU in July
  // can make it again in September, and the in-window set alone would wrongly rule that out.
  const canMake = {};
  for (const f of FACILITIES) canMake[f] = new Set();
  for (const ymd of prodDaysSorted) for (const f of FACILITIES) {
    for (const [sku, q] of Object.entries((dailyProduction[ymd] || {})[f] || {})) if (q > 0) canMake[f].add(sku);
  }
  // Group the production calendar into runs.
  const runs = [];
  for (const f of FACILITIES) {
    let cur = null;
    for (const ymd of prodDaysSorted) {
      if (!inProdWindow(ymd)) continue;
      const d = (dailyProduction[ymd] || {})[f] || {};
      const active = SKUS.filter(s => (d[s] || 0) > 0);
      const sku = active.length === 1 ? active[0] : null;   // a mixed day ends a run
      const qty = active.reduce((s, k) => s + d[k], 0);
      if (!sku) { if (cur) { runs.push(cur); cur = null; } continue; }
      if (cur && cur.f === f && cur.sku === sku && ymdToSerial(ymd) === ymdToSerial(cur.end) + 1) {
        cur.end = ymd; cur.days++; cur.qty += qty;
      } else { if (cur) runs.push(cur); cur = { f, sku, start: ymd, end: ymd, days: 1, qty }; }
    }
    if (cur) runs.push(cur);
  }
  const firstChangeable = addDaysYMD(todayYMD, PROD_LEAD_DAYS);
  const scarceSkus = SKUS.filter(s => skuIsScarce[s] && poShortBy[s] > 0.5)
    .sort((a, b) => poShortBy[b] - poShortBy[a]);

  for (const scarce of scarceSkus) {
    const gap = Math.round(poShortBy[scarce]);
    // Earliest changeable run of a SURPLUS SKU, at a facility that can actually make the scarce one.
    const cands = runs.filter(r => r.start >= firstChangeable && !skuIsScarce[r.sku] && canMake[r.f].has(scarce))
      .sort((a, b) => (a.start < b.start ? -1 : a.start > b.start ? 1 : 0));
    if (!cands.length) { prodSuggestions.push({ scarce, gap, none: true }); continue; }
    const r = cands[0];
    const yielded = Math.round(r.qty * PRODUCTION_YIELD);
    // Does the donor SKU stay in surplus after giving up this run?
    const donorSlack = supplyOf(r.sku) - demandOf(r.sku);
    prodSuggestions.push({ scarce, gap, run: r, yielded, donorSlack: Math.round(donorSlack - yielded) });
  }
}

// Measure each suggestion by actually re-running the plan with the run swapped, rather than
// estimating arithmetically — a raw unit count ignores routing and timing and would read optimistic.
// The child gets --no-suggest, which is what stops this recursing.
for (const s of prodSuggestions) {
  if (s.none || !s.run) continue;
  try {
    const { execFileSync } = require('child_process');
    const spec = `${s.run.f}:${s.run.start}:${s.run.end}:${s.run.sku}>${s.scarce}`;
    const out = execFileSync(process.execPath, [
      // --rows, not positional xlsx paths; and deliberately NO --json: the child would otherwise
      // overwrite the parent's output file with its own swapped-scenario plan. Its result is read
      // back off stdout, exactly as the skill does.
      process.argv[1], '--rows', rowsPath, '--no-suggest', '--swap-run', spec,
      // Match the parent's today's-production mode (including a partial, single-facility exclusion)
      // so the measured before/after comparison stays apples-to-apples instead of the child silently
      // reverting to "include everywhere".
      ...(TODAY_PRODUCTION_EXCLUDED_FACILITIES.length
        ? [`--no-today-production=${TODAY_PRODUCTION_EXCLUDED_FACILITIES.join(',')}`]
        : []),
      // Same reason: if the parent counts Hold as dispatchable, the child must too, or the
      // "fill % after" it measures would be against a scenario with less stock than this plan has.
      ...(INCLUDE_HOLD ? ['--include-hold'] : []),
      // stdio: capture the child's stderr instead of inheriting it. Without this, anything the
      // child warns about is printed into THIS run's report as if the real plan had the problem.
    ], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, env: process.env, stdio: ['ignore', 'pipe', 'pipe'] });
    const m = out.match(/^\s+TOTAL\s+([\d]+)\s+([\d]+)\s+([\d]+)\s+([\d.]+)/m);
    if (m) s.after = { ordered: +m[1], served: +m[2], short: +m[3], fill: +m[4] };
    const missed = out.match(/Missed orders \((\d+)\)/);
    if (missed) s.afterMissed = +missed[1];
  } catch (e) {
    s.simError = String(e.message).split('\n')[0];
  }
}

if (prodSuggestions.length) {
  console.log('\n\n┌─ PRODUCTION CHANGE SUGGESTIONS  (run-wise; only runs starting ' + PROD_LEAD_DAYS + '+ days out are changeable)\n');
  for (const s of prodSuggestions) {
    if (s.none) {
      console.log(`  ${pad(s.scarce, 14)} short ${fmt0(s.gap)} units — no changeable run of a surplus SKU at a facility that can make it.`);
      continue;
    }
    const r = s.run;
    console.log(`  ${s.scarce} is short ${fmt0(s.gap)} units and cannot be fixed by routing. Suggested change:`);
    console.log('');
    console.log(`     ${r.f}   ${ymdToLabel(r.start)} .. ${ymdToLabel(r.end)}   (${r.days}-day run)`);
    console.log(`     switch  ${r.sku}  →  ${s.scarce}`);
    console.log(`     ${fmt0(r.qty)} units raw  →  ${fmt0(s.yielded)} units after ${PRODUCTION_YIELD * 100}% yield  (covers ${Math.min(100, Math.round(100 * s.yielded / s.gap))}% of the gap)`);
    console.log(`     ${r.sku} keeps ~${fmt0(s.donorSlack)} units of surplus after the switch${s.donorSlack > 0 ? '' : '  ⚠️  WOULD GO SHORT — do not switch'}`);
    if (s.after) {
      console.log('');
      console.log(`     MEASURED by re-running the plan with this change:`);
      console.log(`       PO fill rate   ${(100 * srvTot / ordTot).toFixed(1)}%  →  ${s.after.fill.toFixed(1)}%`);
      console.log(`       units short    ${fmt0(termTotals.short)}  →  ${fmt0(s.after.short)}   (${fmt0(termTotals.short - s.after.short)} units rescued)`);
      if (s.afterMissed !== undefined) console.log(`       missed orders  ${missedPOEvents.length}  →  ${s.afterMissed}`);
      // A swap that measures WORSE is not a suggestion. It happens because the donor SKU was
      // carrying its own committed orders, or because the new stock lands after the deadlines it
      // was meant to save. Say "do not make this change" rather than leaving a recommendation
      // sitting in the sheet that the plant might action.
      const delta = termTotals.short - s.after.short;
      if (delta <= 0) {
        console.log('');
        console.log(`     ❌ DO NOT MAKE THIS CHANGE — measured ${delta === 0 ? 'no improvement' : 'a NET LOSS of ' + fmt0(-delta) + ' units'}.`);
        console.log(`        The donor SKU's own committed orders, or the arrival timing, cost more than the swap gains.`);
        console.log(`        Shown only so the option is on record as tested and rejected.`);
      }
    } else if (s.simError) {
      console.log(`     (could not re-simulate: ${s.simError})`);
    }
    // The run is dispatchable the day after it ends, then needs transit — so it cannot rescue any
    // order dated before that. Say so rather than implying the whole gap closes.
    const reachFrom = addDaysYMD(r.end, 1 + Math.min(...WH_ORDER.map(w => TAT[r.f][w])));
    const unreachable = missedPOEvents.filter(m => m.sku === s.scarce && m.day < reachFrom);
    if (unreachable.length) {
      const u = unreachable.reduce((a, m) => a + m.short, 0);
      console.log(`     Cannot reach ${fmt0(u)} units already missed before ${ymdToLabel(reachFrom)} (${unreachable.map(m => ymdToLabel(m.day)).join(', ')})`);
      console.log(`     — those need production inside the ${PROD_LEAD_DAYS}-day lock.`);
    }
    console.log('');
  }
}

console.log('\n\n┌─ METHOD & ASSUMPTIONS');
console.log('│  - Trucks are single-destination, mixed-SKU, sized 500-725 units; a facility\'s undispatched');
console.log('│    residual carries forward day to day until it can form a full truck.');
console.log('│  - Destination + SKU fill order are driven by one strict (tier, size) ladder per WH×SKU:');
console.log('│      tier 0  a committed PO would go unfulfilled — no floor, always first');
DOI_LADDER.forEach((d, i) => {
  const from = i === 0 ? '0' : DOI_LADDER[i - 1];
  console.log(`│      tier ${i + 1}  cover between ${pad(String(from), 2)} and ${pad(String(d), 2)} days — top up to ${d} DOI`);
});
console.log(`│      tier ${LADDER_FALLBACK_TIER}  past ${DOI_LADDER[DOI_LADDER.length - 1]} DOI (or no ambient DRR) — pile-gap toward WH_SPLIT`);
console.log('│            (Bangalore 30% / Hyderabad 20% / Mumbai 20% / Gurgaon 20% / Kolkata 10%)');
console.log(`│      tier ${NOTHING_TIER}  nothing needed anywhere — days sat at the plant is the only tiebreak`);
console.log('│    Ranking takes a warehouse\'s BEST tier, never a sum across tiers, so no warehouse can be');
console.log(`│    pushed toward ${DOI_LADDER[1] || DOI_LADDER[0]} DOI while another still sits below ${DOI_LADDER[0]}. Each SKU is topped up only`);
console.log('│    to the ceiling of the rung it is currently in, so trucks spread across whatever is most');
console.log('│    deprived instead of filling one warehouse to the top. DOI is measured on ambient DRR');
console.log('│    alone, at the truck\'s real ETA and net of stock already in transit; committed POs are');
console.log('│    handled separately by tier 0. --doi-ladder retunes the rungs, --no-doi disables them.');
console.log('│  - EMERGENCY = tier 0 or tier 1 (a real PO shortfall, or below the hard ' + DOI_LADDER[0] + '-DOI floor).');
console.log('│    Everything above is PILE-UP and never repeats the same destination twice in a row,');
console.log('│    tracked globally across both facilities (see the Reason column).');
console.log(`│  - PO urgency is scored over a PRODUCTION-AWARE horizon: at least ${SCORING_HORIZON_DAYS} days, extended to`);
console.log('│    however long until freshly-made stock of that SKU could next reach a warehouse. While a');
console.log('│    SKU is still in production this stays at the minimum; once its last run has passed, the');
console.log('│    horizon stretches to the end of the simulation so every remaining committed order is');
console.log('│    visible while stock still exists to hold for it (tier 0 outranks every DOI rung, so no');
console.log('│    separate reservation is needed). It never shrinks below the minimum. DOI rungs are');
console.log('│    unaffected — they keep measuring cover at the truck\'s real ETA. See PRODUCTION OUTLOOK.');
console.log(`│  - ${JOINT_PLANTS ? 'BOTH PLANTS are considered together' : 'Run with --no-joint: plants decided sequentially'}: every (facility, warehouse) pair competes on the`);
console.log('│    same ladder, so a destination goes to whichever plant serves it best, at every tier');
console.log('│    including PO emergencies. Previously Ronch formed all its trucks first, giving it first');
console.log('│    pick of destination every day. The horizon is common to all candidates so a longer lane');
console.log('│    no longer inflates its own apparent deficit.');
console.log(`│  - Lane preference: each excess transit day over the warehouse's fastest plant discounts`);
console.log(`│    that destination's need by a factor of 1/(1+${LANE_WEIGHT}·days) — a soft, tradeable cost, so a`);
console.log('│    warehouse with genuinely larger need still wins a slower lane. Applied to score only,');
console.log('│    never to the raw need that labels EMERGENCY vs PILE-UP, and bounded above zero so a');
console.log('│    real PO shortfall can never be discounted out of top priority. See LANE EFFICIENCY.');
console.log('│  - Each plant\'s pool OPENS at its FG from WH-Channel-SKU → Current Inventory → the');
console.log(INCLUDE_HOLD
  ? '│    plant FG/Hold table. This run used --include-hold, so "<facility> Hold" stock was treated\n│    as DISPATCHABLE from day one alongside FG — valid only if that blocker is genuinely\n│    cleared (the amount is named under OPENING FG above).'
  : '│    plant FG/Hold table, FG rows only. "<facility> Hold" is real stock but is NOT\n│    dispatchable and is excluded (the amount is named under OPENING FG above so the\n│    exclusion stays visible).');
console.log('│    FG is leftover stock from previous days, with today\'s');
console.log('│    production added on top via the D+1 rule — the two only avoid double-counting if the');
console.log('│    FG snapshot predates today\'s finished production; --no-today-production excludes');
console.log('│    today\'s row for a same-day re-run after the snapshot already reflects it (see banner');
console.log('│    above for which mode this run used). NO yield factor is applied to FG — it is');
console.log('│    already-finished, counted stock. The table carries no timestamp, so the printed values');
console.log('│    remain the check; --no-fg opts out of FG seeding entirely.');
console.log('│  - 90% yield applied to Day wise trackr\'s daily "Actual Production Ronch/Amber" figures;');
console.log('│    a day D\'s production becomes dispatchable on D+1, then delayed by facility→WH TAT.');
console.log('│  - PO demand = Raw Data Sheet, movement type MM, origin = warehouse, channel in');
console.log('│    {Amazon, Flipkart, Croma, Vijay Sales, Reliance} — confirmed whitelist. Excluded:');
console.log('│    Primarc, Rentomojo, DTDC-Raftaar (dark-store demand handled via DS DRR instead, not');
console.log('│    as a forward PO signal — same precedent as inventory-alert), and channel="NA"');
console.log('│    (RO_PLS_*/RO_GT_*/SL_GT_*) rows, assumed already reflected in the warehouse-level DRR.');
console.log('│  - Dark-store DRR is added to its parent warehouse\'s effective daily outflow, not');
console.log('│    separately balance-tracked; dark-store on-hand is not netted into the warehouse\'s');
console.log('│    opening balance (conservative — may slightly overstate near-term urgency).');
console.log('│  - DRR (Diwali Sales Plan trailing-15-day) held constant across the whole window.');
console.log('│  - In-transit pipeline ETA backtracked from Raw Data Sheet FM-type AMBER/RONCH-origin');
console.log('│    dispatch records, matched against Dispatch Planning\'s aggregate in-transit total.');
console.log('│  - M3 (non-Pro) PO demand is captured (Raw Data Sheet col X) — unlike sibling skills\' gap.');
console.log('│  - Facility SKU mix is read as-is (e.g. an occasional Amber M0 production day dispatches');
console.log('│    as-is, not restricted to the "normal" Amber M1 Pro/M2 Pro mix).');
console.log('');

// ═══════════════════════════════════════════════════════════════════════
// JSON export (replaces the skill's optional XLSX export)
//
// Every tab below is built EXACTLY as the skill builds it for the workbook -- same rows, same
// order, same headers -- so the portal renders the skill's own numbers rather than a paraphrase.
// The only change is where they land: three shims turn the xlsx calls into a plain
// { "<tab name>": [[...rows]] } map. Keeping the builders byte-identical is what makes re-vendoring
// a copy-paste rather than a merge.
// ═══════════════════════════════════════════════════════════════════════
{
  const wbOut = { Sheets: {}, SheetNames: [] };
  const aoa_to_sheet = (rows) => rows;
  const book_append_sheet = (wb, rows, name) => { wb.Sheets[name] = rows; wb.SheetNames.push(name); };

  const planRows = [['Dispatch Date', 'Facility', 'Warehouse', 'SKU', 'Qty', 'Truck Total', 'ETA', 'Reason']];
  for (const ev of dispatchPlan) {
    for (const [sku, qty] of Object.entries(ev.skus)) {
      planRows.push([ymdToLabel(ev.date), ev.facility, ev.wh, sku, qty, ev.total, ymdToLabel(ev.eta), ev.reason]);
    }
  }
  const planWs = aoa_to_sheet(planRows);
  planWs['!cols'] = [{ wch: 14 }, { wch: 9 }, { wch: 11 }, { wch: 13 }, { wch: 8 }, { wch: 11 }, { wch: 14 }, { wch: 10 }];
  book_append_sheet(wbOut, planWs, 'Dispatch Plan');

  const utilRows = [['Facility', 'Opening FG', 'Opening FG Breakdown', 'Production (x' + PRODUCTION_YIELD + ')', 'Dispatched Units', 'Trucks', 'Residual Units', 'Residual Breakdown']];
  for (const f of FACILITIES) {
    const dispatched = dispatchPlan.filter(e => e.facility === f).reduce((s, e) => s + e.total, 0);
    const truckCount = dispatchPlan.filter(e => e.facility === f).length;
    const leftover = Object.values(pool[f]).reduce((a, b) => a + b, 0);
    const breakdown = Object.entries(pool[f]).filter(([, q]) => q > 0).map(([s, q]) => `${s}:${fmt0(q)}`).join(', ');
    const fgTot = fgTotalOf(f);
    const fgBreak = SKUS.filter(s => (currentFG[f] || {})[s] > 0).map(s => `${s}:${fmt0(currentFG[f][s])}`).join(', ');
    // Opening FG + yielded production = dispatched + residual.
    utilRows.push([f, fgTot, fgBreak, dispatched + leftover - fgTot, dispatched, truckCount, leftover, breakdown]);
  }
  const utilWs = aoa_to_sheet(utilRows);
  utilWs['!cols'] = [{ wch: 9 }, { wch: 11 }, { wch: 40 }, { wch: 16 }, { wch: 16 }, { wch: 8 }, { wch: 14 }, { wch: 30 }];
  book_append_sheet(wbOut, utilWs, 'Facility Utilization');

  const agedRows = [['Date', 'Facility', 'SKU', 'Qty', 'Age Days']];
  for (const c of agedClearances) agedRows.push([ymdToLabel(c.date), c.facility, c.sku, c.qty, c.ageDays]);
  if (agedClearances.length === 0) agedRows.push([`None — no SKU sat unshipped for ${MAX_HOLD_DAYS}+ days`, '', '', '', '']);
  const agedWs = aoa_to_sheet(agedRows);
  agedWs['!cols'] = [{ wch: 14 }, { wch: 9 }, { wch: 13 }, { wch: 8 }, { wch: 10 }];
  book_append_sheet(wbOut, agedWs, 'Age At Shipment');

  // Replaces the old Stockout Risk sheet — with stock floored at zero, "worst balance" no longer
  // means anything. Fill rate is the number to check.
  const fillRows = [['SKU', 'PO Ordered', 'PO Served', 'Short', 'Fill %', 'Hard Supply Deficit', 'Dispatch-Fixable']];
  const fillTotals = { ordered: 0, served: 0, short: 0 };
  for (const sku of SKUS) {
    if (poOrdered[sku] <= 0 && poShortBy[sku] <= 0) continue;
    const netDrr = WH_ORDER.reduce((s, wh) => s + effDrr[wh][sku], 0);
    const hard = Math.max(0, Math.round(poOrdered[sku] + netDrr * simDays - supplyOf(sku)));
    // Derive the row from ROUNDED components so `Ordered - Served = Short` holds exactly as
    // printed, then total the rounded rows rather than rounding the float total separately —
    // otherwise the column visibly fails to sum to its own TOTAL by a unit or two.
    const servedR = Math.round(poServed[sku]);
    const short = poOrdered[sku] - servedR;
    fillTotals.ordered += poOrdered[sku]; fillTotals.served += servedR; fillTotals.short += short;
    fillRows.push([sku, poOrdered[sku], servedR, short,
      poOrdered[sku] > 0 ? Math.round(1000 * poServed[sku] / poOrdered[sku]) / 10 : 100,
      Math.min(hard, short), Math.max(0, short - hard)]);
  }
  fillRows.push(['TOTAL', fillTotals.ordered, fillTotals.served, fillTotals.short,
    fillTotals.ordered > 0 ? Math.round(1000 * fillTotals.served / fillTotals.ordered) / 10 : 100, hardTot, fixTot]);
  fillRows.push([]);
  fillRows.push(['Missed orders — date, warehouse, SKU, ordered, served, short']);
  if (!missedPOEvents.length) fillRows.push(['None — every committed order served in full and on time', '', '', '', '', '', '']);
  for (const m of missedPOEvents) fillRows.push([ymdToLabel(m.day), m.wh, m.sku, m.ordered, Math.round(m.served), Math.round(m.short), '']);
  fillRows.push([]);
  fillRows.push(['Days at zero stock — warehouse, SKU, days, ambient demand lost']);
  for (const wh of WH_ORDER) for (const sku of SKUS) {
    if (daysAtZero[wh][sku] > 0) fillRows.push([wh, sku, daysAtZero[wh][sku], Math.round(unmetDrr[wh][sku]), '', '', '']);
  }
  const fillWs = aoa_to_sheet(fillRows);
  fillWs['!cols'] = [{ wch: 30 }, { wch: 14 }, { wch: 12 }, { wch: 10 }, { wch: 9 }, { wch: 20 }, { wch: 17 }];
  book_append_sheet(wbOut, fillWs, 'PO Fill Rate');

  const laneHdr = [['Date', 'Facility', 'Warehouse', 'Units', 'TAT Used', 'Best TAT', 'Excess Days', 'Swappable Units', 'Forced Units']];
  for (const r of laneRows) laneHdr.push([ymdToLabel(r.date), r.facility, r.wh, r.total, r.used, r.bestT, r.excess, r.swap, r.forced]);
  if (laneRows.length === 0) laneHdr.push(['None — every truck is on its warehouse\'s fastest lane', '', '', '', '', '', '', '', '']);
  laneHdr.push([]);
  laneHdr.push(['Transit in plan (unit-days)', laneUnitDays]);
  laneHdr.push(['Lane floor (unit-days)', laneFloorDays]);
  laneHdr.push(['% above floor', laneFloorDays ? Math.round(1000 * (laneUnitDays - laneFloorDays) / laneFloorDays) / 10 : 0]);
  laneHdr.push(['Slow-lane units genuinely swappable (other plant makes that SKU)', swappableSlow]);
  laneHdr.push(['Slow-lane units forced (single-source SKU)', forcedSlow]);
  laneHdr.push(['Recoverable if swappable volume moved to its natural lane (unit-days)', swappableExcessDays]);
  laneHdr.push(['Lane weight in force (per excess day; 0 disables)', LANE_WEIGHT]);
  const laneWs = aoa_to_sheet(laneHdr);
  laneWs['!cols'] = [{ wch: 34 }, { wch: 9 }, { wch: 12 }, { wch: 8 }, { wch: 10 }, { wch: 10 }, { wch: 12 }, { wch: 16 }, { wch: 13 }];
  book_append_sheet(wbOut, laneWs, 'Lane Efficiency');

  const doiRows = [['Warehouse', 'SKU', 'DRR/day', 'Opening Balance', 'Opening DOI', 'Closing Balance', 'Closing DOI', 'Rung Reached']];
  for (const wh of WH_ORDER) for (const sku of SKUS) {
    const drr = effDrr[wh][sku];
    if (!(drr > 0) && openingBalance[wh][sku] <= 0 && balance[wh][sku] <= 0) continue;
    const o = doiOf(openingBalance[wh][sku], wh, sku), c = doiOf(balance[wh][sku], wh, sku);
    doiRows.push([wh, sku, Math.round(drr * 10) / 10, Math.round(openingBalance[wh][sku]),
      isFinite(o) ? Math.round(o * 10) / 10 : '', Math.round(balance[wh][sku]),
      isFinite(c) ? Math.round(c * 10) / 10 : '', rungOf(c)]);
  }
  doiRows.push([]);
  doiRows.push(['Ladder (days of cover)', DOI_ENABLED ? DOI_LADDER.join(' / ') : 'DISABLED (--no-doi)']);
  doiRows.push(['Weakest warehouse per SKU — min opening DOI vs min closing DOI', '']);
  for (const sku of SKUS) {
    const rows = WH_ORDER.filter(wh => effDrr[wh][sku] > 0);
    if (!rows.length) continue;
    let o = Infinity, c = Infinity, at = '';
    for (const wh of rows) {
      o = Math.min(o, doiOf(openingBalance[wh][sku], wh, sku));
      const cc = doiOf(balance[wh][sku], wh, sku);
      if (cc < c) { c = cc; at = wh; }
    }
    doiRows.push(['', sku, '', '', isFinite(o) ? Math.round(o * 10) / 10 : '', '', isFinite(c) ? Math.round(c * 10) / 10 : '', at]);
  }
  const doiWs = aoa_to_sheet(doiRows);
  doiWs['!cols'] = [{ wch: 34 }, { wch: 14 }, { wch: 9 }, { wch: 16 }, { wch: 12 }, { wch: 16 }, { wch: 12 }, { wch: 14 }];
  book_append_sheet(wbOut, doiWs, 'DOI Coverage');

  const prodRows = [['Facility', 'SKU', 'Runs', 'Last Run', 'Idle Days After', 'Units Made']];
  for (const sku of SKUS) for (const f of FACILITIES) {
    let last = null, units = 0, runs = 0;
    for (const ymd of prodDaysSorted) {
      if (!inProdWindow(ymd)) continue;
      const q = (((dailyProduction[ymd] || {})[f] || {})[sku] || 0);
      if (q > 0) { last = ymd; units += q; runs++; }
    }
    if (!runs) continue;
    prodRows.push([f, sku, runs, ymdToLabel(last), ymdToSerial(PROD_LAST) - ymdToSerial(last), units]);
  }
  prodRows.push([]);
  prodRows.push(['Committed PO demand landing AFTER the last production run anywhere']);
  prodRows.push(['SKU', 'Last Run Anywhere', 'PO Demand After', 'Note']);
  if (!cliffs.length) prodRows.push(['None — every SKU has production beyond its remaining committed demand', '', '', '']);
  for (const c of cliffs) {
    prodRows.push([c.sku, c.last ? ymdToLabel(c.last) : 'never', c.after,
      c.last ? 'Must ship from stock made on/before that date' : 'No production at all this window — cannot be fixed by dispatch']);
  }
  const prodWs = aoa_to_sheet(prodRows);
  prodWs['!cols'] = [{ wch: 64 }, { wch: 18 }, { wch: 16 }, { wch: 16 }, { wch: 16 }, { wch: 12 }];
  book_append_sheet(wbOut, prodWs, 'Production Outlook');

  const mixSheetRows = [['SKU', 'Committed PO', 'Ambient DRR', 'Total Demand', 'Raw RONCH', 'Raw AMBER', 'Raw Total', 'All-Source Supply', 'Gap']];
  for (const m of mixRows) {
    mixSheetRows.push([m.sku, Math.round(m.po), Math.round(m.ambient), Math.round(m.demand),
      Math.round(m.rawByFac.RONCH), Math.round(m.rawByFac.AMBER), Math.round(m.rawTot),
      Math.round(m.supply), Math.round(m.supply - m.demand)]);
  }
  mixSheetRows.push([]);
  mixSheetRows.push([`Gap = all-source supply (on-hand + in-transit + plant FG + production x${PRODUCTION_YIELD}) minus committed PO plus ambient DRR, over ${ymdToLabel(WINDOW_START)}..${ymdToLabel(SIM_BUFFER_END)}. A negative gap cannot be fixed by routing, only by making more of that SKU. A large positive gap is stock that still has to be trucked and warehoused — cross-read it against the closing DOI figures.`]);
  const mixWs = aoa_to_sheet(mixSheetRows);
  mixWs['!cols'] = [{ wch: 16 }, { wch: 14 }, { wch: 13 }, { wch: 14 }, { wch: 12 }, { wch: 12 }, { wch: 11 }, { wch: 18 }, { wch: 10 }];
  book_append_sheet(wbOut, mixWs, 'Production Mix');

  const sugRows =[['Scarce SKU', 'Units Short', 'Facility', 'Run Start', 'Run End', 'Days', 'Switch From', 'Switch To', 'Units Raw', 'Units Yielded', 'Donor Surplus After', 'Fill % Before', 'Fill % After', 'Units Rescued']];
  for (const s of prodSuggestions) {
    if (s.none || !s.run) { sugRows.push([s.scarce, Math.round(s.gap), 'no changeable run of a surplus SKU at a facility that can make it', '', '', '', '', '', '', '', '', '', '', '']); continue; }
    const r = s.run;
    sugRows.push([s.scarce, Math.round(s.gap), r.f, ymdToLabel(r.start), ymdToLabel(r.end), r.days, r.sku, s.scarce,
      r.qty, s.yielded, s.donorSlack,
      Math.round(1000 * fillTotals.served / fillTotals.ordered) / 10, s.after ? s.after.fill : '', s.after ? Math.round(fillTotals.short - s.after.short) : '']);
  }
  if (prodSuggestions.length === 0) sugRows.push(['None — no SKU is short beyond what routing can fix', '', '', '', '', '', '', '', '', '', '', '', '', '']);
  sugRows.push([]);
  sugRows.push([`Only runs starting ${PROD_LEAD_DAYS}+ days out are treated as changeable; a run is consecutive days of one SKU at one facility. "Fill % After" is measured by re-running the whole plan with the swap applied, not estimated.`]);
  const sugWs = aoa_to_sheet(sugRows);
  sugWs['!cols'] = [{ wch: 52 }, { wch: 12 }, { wch: 10 }, { wch: 14 }, { wch: 14 }, { wch: 6 }, { wch: 13 }, { wch: 13 }, { wch: 11 }, { wch: 14 }, { wch: 19 }, { wch: 13 }, { wch: 12 }, { wch: 14 }];
  book_append_sheet(wbOut, sugWs, 'Production Suggestions');

  const assumptionRows = [['Method & Assumptions']];
  assumptionRows.push(['Trucks are single-destination, mixed-SKU, sized 500-725 units; a facility\'s undispatched residual carries forward day to day until it can form a full truck.']);
  assumptionRows.push(['Destination and SKU fill order are both driven by one unified score per WH×SKU, in strict tier order: PO-driven need > ambient DRR-driven need > pile-gap toward the WH_SPLIT target network share (Bangalore 30% / Hyderabad 20% / Mumbai 20% / Gurgaon 20% / Kolkata 10%, a business target, not derived from DRR) > days sat unshipped at the plant, which only ever tiebreaks when everything above ties at zero.']);
  assumptionRows.push([DOI_ENABLED
    ? `Replenishment follows a strict DOI ladder: tier 0 = a committed PO would go unfulfilled (no floor, always first), then rungs at ${DOI_LADDER.join('/')} days of cover, then a fallback to the WH_SPLIT network-share pile-gap once a warehouse×SKU is past ${DOI_LADDER[DOI_LADDER.length - 1]} DOI or has no ambient DRR. Destination ranking takes a warehouse's BEST tier rather than a sum across tiers, so no warehouse can be pushed toward ${DOI_LADDER[1] || DOI_LADDER[0]} DOI while another still sits below ${DOI_LADDER[0]}. Each SKU is topped up only to the ceiling of the rung it is currently in, so a truck spreads across whatever is most deprived instead of filling one warehouse to the top.`
    : 'Run with --no-doi: the DOI ladder was disabled and replenishment used the WH_SPLIT network-share pile-gap alone.']);
  assumptionRows.push([`PO urgency is scored over a production-aware horizon: at least ${SCORING_HORIZON_DAYS} days, extended to however long until freshly-made stock of that SKU could next reach a warehouse. While a SKU is still in production this stays at the minimum; once its last run has passed the horizon stretches to the end of the simulation, so every remaining committed order becomes visible while stock still exists to hold for it. Tier 0 outranks every DOI rung by construction, so no separate reservation mechanism is needed. DOI rungs are unaffected and keep measuring cover at the truck's real ETA. See the Production Outlook sheet.`]);
  assumptionRows.push([JOINT_PLANTS
    ? 'Both plants are considered together: every (facility, warehouse) pair competes on the same ladder each time a truck is formed, so a destination goes to whichever plant serves it best — applied at every tier, PO emergencies included. Previously Ronch formed all of its trucks first, giving it first pick of destination every single day.'
    : 'Run with --no-joint: plants were decided sequentially (all of Ronch\'s trucks, then all of Amber\'s), reproducing the pre-joint baseline.']);
  assumptionRows.push([`DOI is measured on ambient DRR only, evaluated at the truck's real ETA and net of stock already scheduled to arrive. Committed POs are handled separately by tier 0, so the ladder is safety stock on top of committed orders. Calibration note: PO demand runs several times ambient DRR, so a ${DOI_LADDER[0]}-DOI rung is a much smaller buffer in real throughput terms than it appears.`]);
  assumptionRows.push([`EMERGENCY = tier 0 or tier 1 (a real PO shortfall, or below the hard ${DOI_LADDER[0]}-DOI floor). Everything above that is PILE-UP, and a PILE-UP pick never repeats the same destination twice in a row — tracked globally across both facilities so they can't independently double-dump the same warehouse on the same day.`]);
  assumptionRows.push([`Every candidate destination is scored over one common ${SCORING_HORIZON_DAYS}-day forward horizon rather than its own arrival date. Previously the horizon was day+TAT, so a longer lane walked more days, accumulated more PO/DRR demand and produced a larger apparent deficit — systematically pushing each plant toward its most distant warehouses.`]);
  assumptionRows.push([`Lane preference: each excess transit day over the warehouse's fastest plant (Ronch: Mumbai 2d/Hyderabad 3d/Bangalore 4d; Amber: Gurgaon 2d/Kolkata 5d) discounts that destination's need by a factor of 1/(1+${LANE_WEIGHT}*days). This is a soft, tradeable cost — a warehouse with genuinely larger need still wins a slower lane. It is applied to the ranking score only, never to the raw need that labels EMERGENCY vs PILE-UP, and is bounded above zero so a real PO shortfall can never be discounted out of top priority. See the Lane Efficiency sheet.`]);
  assumptionRows.push([FG_ENABLED
    ? `Each plant's pool opens at its FG from WH-Channel-SKU → Current Inventory → the plant FG/Hold table, FG rows only — opening FG this run: ${FACILITIES.map(f => `${f} ${fmt0(fgTotalOf(f))}`).join(', ')}. ${INCLUDE_HOLD
      ? `"<facility> Hold" rows were INCLUDED as dispatchable from day one this run via --include-hold (${fmt0(FACILITIES.reduce((s, f) => s + holdTotalOf(f), 0))} units). Hold is normally blocked stock, so this plan is only valid if that blocker has actually been cleared and the units can physically ship.`
      : `"<facility> Hold" rows are real stock but are NOT dispatchable and are excluded (${fmt0(FACILITIES.reduce((s, f) => s + holdTotalOf(f), 0))} units this run); --include-hold overrides that once the blocker is cleared.`} FG is leftover stock from previous days, with today's production added on top via the D+1 rule — this run ${TODAY_PRODUCTION_EXCLUDED_FACILITIES.length === 0 ? 'INCLUDED' : TODAY_PRODUCTION_EXCLUDED_FACILITIES.length === FACILITIES.length ? 'EXCLUDED' : `INCLUDED for ${FACILITIES.filter(f => !TODAY_PRODUCTION_EXCLUDED_FACILITIES.includes(f)).join(', ')} and EXCLUDED for ${TODAY_PRODUCTION_EXCLUDED_FACILITIES.join(', ')}`} today's (${ymdToLabel(todayYMD)}) production row, on the assumption that the FG snapshot predates it where included and already reflects it where excluded (--no-today-production, optionally scoped to one facility via --no-today-production=FACILITY, toggles this per run). No yield factor is applied to FG because it is already-finished, physically counted stock. The parse is reconciled against the table's own total row each run. The table carries no timestamp, so the printed values remain the check, and --no-fg disables the seeding.`
    : 'Run with --no-fg: plant pools opened EMPTY and the Current Inventory plant FG/Hold table was ignored.']);
  assumptionRows.push(['90% yield applied to Day wise trackr\'s daily "Actual Production Ronch/Amber" figures; a day D\'s production becomes dispatchable on D+1, then delayed by facility→WH TAT.']);
  assumptionRows.push(['PO demand = Raw Data Sheet, movement type MM, origin = warehouse, channel in {Amazon, Flipkart, Croma, Vijay Sales, Reliance} — confirmed whitelist. Excluded: Primarc, Rentomojo, DTDC-Raftaar (dark-store demand handled via DS DRR instead), and channel="NA" (RO_PLS_*/RO_GT_*/SL_GT_*) rows.']);
  assumptionRows.push(['Dark-store DRR is added to its parent warehouse\'s effective daily outflow, not separately balance-tracked; dark-store on-hand is not netted into the warehouse\'s opening balance.']);
  assumptionRows.push(['DRR (Diwali Sales Plan trailing-15-day) held constant across the whole window.']);
  assumptionRows.push(['In-transit pipeline ETA backtracked from Raw Data Sheet FM-type AMBER/RONCH-origin dispatch records, matched against Dispatch Planning\'s aggregate in-transit total.']);
  assumptionRows.push(['M3 (non-Pro) PO demand is captured (Raw Data Sheet col X) — unlike sibling skills\' gap.']);
  assumptionRows.push(['Facility SKU mix is read as-is (e.g. an occasional Amber M0 production day dispatches as-is, not restricted to the "normal" Amber M1 Pro/M2 Pro mix).']);
  const assumptionWs = aoa_to_sheet(assumptionRows);
  assumptionWs['!cols'] = [{ wch: 120 }];
  book_append_sheet(wbOut, assumptionWs, 'Assumptions');

  // The report tabs, plus four things the tabs don't carry and the portal needs:
  //  - dispatchPlan raw: richer than the flattened Dispatch Plan rows (one object per truck, with
  //    the SKU map intact and the numeric tier the EMERGENCY/PILE-UP string flattens away).
  //  - currentFG / currentHold: never mutated by the simulation, unlike `pool`, which by this point
  //    holds the END-of-window residual. These are the opening plant figures the tab shows.
  //  - todayProduction: captured before --no-today-production can delete it, so both scenarios can
  //    show what was actually produced today.
  const out = {
    generatedAt: new Date().toISOString(),
    windowStart: WINDOW_START,
    windowEnd: WINDOW_END,
    poCutoff: PO_CUTOFF,
    todayYMD,
    todayProductionIncluded: TODAY_PRODUCTION_ENABLED,
    productionYield: PRODUCTION_YIELD,
    tabs: wbOut.Sheets,
    dispatchPlan,
    missedPORows,
    plant: {
      fg: currentFG,
      hold: currentHold,
      unresolved: fgParse.unresolved,
      found: fgParse.found,
      includeHold: INCLUDE_HOLD,
      todayProduction: TODAY_PRODUCTION_SNAPSHOT,
    },
    poOrderedTotal: SKUS.reduce((t, sku) => t + (poOrdered[sku] || 0), 0),
  };
  if (jsonOutPath) {
    fs.writeFileSync(jsonOutPath, JSON.stringify(out));
    console.log(`\n📄 JSON written to ${jsonOutPath}\n`);
  }
}

// ═══════════════════════════════════════════════════════════════════════
// The skill writes the plan back to the 'First Mile Plan' tab here. Deliberately absent in the
// portal copy: this runs on a cron, and a cron that clears and rewrites a live sheet will sooner
// or later wipe something someone is mid-edit on. The skill stays the only writer of that tab.
// ═══════════════════════════════════════════════════════════════════════
