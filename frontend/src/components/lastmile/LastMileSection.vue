<script setup>
// Last Mile Tracking -- warehouse-to-customer delivery visibility,
// modelled on a working operational report ("Shipment Watch") that
// already covers Blue Dart, Delhivery, DTDC and Shadowfax across
// Native's D2C/UC-App channels. Holisol is deliberately excluded --
// user decision 2026-09-28: it isn't an LSP UC actually uses. Sub-tabbed
// inside AdminApp.vue,
// same convention as S&OP (see SopSection.vue) -- each sub-tab is its
// own component backed by the same useLastMileData.js composable
// (Supabase-polling), fed by scripts/sync_last_mile.py.
//
// Open Shipments is first and the default tab -- the full "not complete,
// not RTO" entry point (every AWB in cohort live/backlog/no_dispatch_date,
// alerted or not). Alerts is a deliberately CURATED subset of that same
// population (only the ones alerts.evaluate() actually flags) -- useful
// for "what needs action right now", but not a substitute for seeing the
// whole tracked population, which is the actual point of this page.
//
// All sections mount at once (v-show, not v-if) so a background poll
// keeps running while another sub-tab is showing -- same convention
// AdminApp.vue itself uses for its top-level nav.
import { ref, computed, onMounted, onUnmounted } from "vue";
import { supabase } from "../../supabaseClient.js";
import LastMileOpenTab from "./LastMileOpenTab.vue";
import LastMileAlertsTab from "./LastMileAlertsTab.vue";
import LastMileWorstLanesTab from "./LastMileWorstLanesTab.vue";

defineProps({
  // Current user's display name, same convention as SlaSection/SparesSection's
  // editor-label -- recorded on the Alerts tab's hand-entered status updates.
  editorLabel: { type: String, default: "" },
});

const SUBTABS = [
  { id: "open", label: "Open Shipments" },
  { id: "alerts", label: "Alerts" },
  { id: "lanes", label: "Worst Lanes" },
];

const activeSubTab = ref(SUBTABS[0].id);

// Freshness banner -- added 2026-10-05 after sync-last-mile.yml's TWO legs
// each silently missed their scheduled tick in the same outage window
// (GitHub Actions can just drop a cron tick under load -- no error, no run
// record, nothing for the workflow to alert on about itself): the hourly
// tracking leg went ~14h without a run (found via a courier's own tracking
// page disagreeing with this dashboard for AWB SF4186299825URM), and that
// same morning's daily watchlist intake (scripts/sync_last_mile_daily.py)
// never ran either (found separately, via last_mile_watchlist.last_pulled_at
// not having moved since the previous day). The two legs fail
// independently, so both get their own line here. A SEPARATE, lightweight
// query (not useLastMileData.js -- that composable fetches all six rollup
// tables, overkill just to show two timestamps) so every sub-tab can show
// this regardless of which one is active.
const lastRunAt = ref(null);
const lastPulledAt = ref(null);
async function refreshFreshness() {
  const [{ data: run }, { data: watch }] = await Promise.all([
    supabase.from("last_mile_run").select("generated_at")
      .order("generated_at", { ascending: false }).limit(1).maybeSingle(),
    // last_mile_watchlist.last_pulled_at, not last_mile_run.last_daily_run_at:
    // the daily leg never writes to last_mile_run at all -- only the hourly
    // leg does -- so that column is permanently null and useless here.
    supabase.from("last_mile_watchlist").select("last_pulled_at")
      .order("last_pulled_at", { ascending: false }).limit(1).maybeSingle(),
  ]);
  lastRunAt.value = run?.generated_at || null;
  lastPulledAt.value = watch?.last_pulled_at || null;
}

// sync-last-mile.yml's hourly leg only runs 10:30-23:30 IST -- outside that
// window a gap is expected, not a fault, so the banner would cry wolf every
// night without this gate. IST is a fixed UTC+5:30 offset (no DST), so this
// is enough without a timezone library.
function inSyncWindow() {
  const ist = new Date(Date.now() + 5.5 * 60 * 60 * 1000);
  const h = ist.getUTCHours() + ist.getUTCMinutes() / 60;
  return h >= 10.5 && h <= 23.5;
}
const HOURLY_STALE_THRESHOLD_MS = 90 * 60 * 1000; // generous past the ~60min cadence, tight enough to catch a real gap same-day
const DAILY_STALE_THRESHOLD_MS = 26 * 60 * 60 * 1000; // above the ~24h cadence (plus the daily cron's own jitter), still same business day

const hourlyAgeMs = computed(() => (lastRunAt.value ? Date.now() - new Date(lastRunAt.value).getTime() : null));
const isHourlyStale = computed(() => hourlyAgeMs.value != null && hourlyAgeMs.value > HOURLY_STALE_THRESHOLD_MS && inSyncWindow());
const dailyAgeMs = computed(() => (lastPulledAt.value ? Date.now() - new Date(lastPulledAt.value).getTime() : null));
const isDailyStale = computed(() => dailyAgeMs.value != null && dailyAgeMs.value > DAILY_STALE_THRESHOLD_MS);

function fmtAge(ms) {
  if (ms == null) return "never";
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m ago`;
}

let freshnessIntervalId = null;
onMounted(async () => {
  await refreshFreshness();
  freshnessIntervalId = setInterval(refreshFreshness, 5 * 60 * 1000);
});
onUnmounted(() => { if (freshnessIntervalId) clearInterval(freshnessIntervalId); });
</script>

<template>
  <div v-if="lastRunAt || lastPulledAt" class="lm-status" :class="{ stale: isHourlyStale || isDailyStale }">
    <span v-if="lastRunAt">
      <b>Tracking last synced:</b> {{ fmtAge(hourlyAgeMs) }}
      <span v-if="isHourlyStale" class="lm-status-hint"> -- expected roughly hourly during 10:30am-11:30pm IST; this gap is past that.</span>
    </span>
    <span v-if="lastPulledAt">
      <b>New AWBs last pulled:</b> {{ fmtAge(dailyAgeMs) }}
      <span v-if="isDailyStale" class="lm-status-hint"> -- expected daily (~9:40am IST); this gap is past that, so recently dispatched shipments may be missing from this page entirely.</span>
    </span>
    <span v-if="isHourlyStale || isDailyStale" class="lm-status-hint">If it doesn't catch up shortly, the sync may need a manual nudge (Actions &gt; Sync Last Mile tracking &gt; Run workflow).</span>
  </div>

  <div class="subtabs">
    <button
      v-for="t in SUBTABS" :key="t.id"
      class="subtab-item" :class="{ active: activeSubTab === t.id }"
      @click="activeSubTab = t.id"
    >{{ t.label }}</button>
  </div>

  <div v-show="activeSubTab === 'open'"><LastMileOpenTab /></div>
  <div v-show="activeSubTab === 'alerts'"><LastMileAlertsTab :editor-label="editorLabel" /></div>
  <div v-show="activeSubTab === 'lanes'"><LastMileWorstLanesTab /></div>
</template>

<style scoped>
/* Same shape as payments.css' .fin-status -- kept local instead of shared
   since this is the only other place using this "sync freshness" banner pattern. */
.lm-status { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; font-size: 0.82rem; }
.lm-status.stale { background: var(--open-soft); border-color: var(--open); }
.lm-status.stale b { color: var(--open); }
.lm-status-hint { color: var(--muted); }
</style>
