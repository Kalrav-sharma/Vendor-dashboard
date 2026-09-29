// SLA › SLA change alerts. Reads sla_pincode_alert for the RCA week on display (written by the
// VPN-side sync_sla_portal.js, step 1c) plus the user's "SLA revised" toggles from
// sla_pincode_revised (browser-written, per week x pincode). A new RCA week starts a fresh list,
// and its toggles start at No; older weeks' rows stay in the tables but aren't shown.
import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

const POLL_INTERVAL_MS = 5 * 60 * 1000;

export function useSlaPincodeAlerts(weekStartRef, editorLabel) {
  const alerts = ref([]);
  const revised = ref({}); // pincode -> row
  const week = ref(null);
  const loaded = ref(false);
  const loadError = ref("");
  const saveError = ref("");

  async function resolveWeek() {
    if (weekStartRef.value) return weekStartRef.value;
    const { data } = await supabase.from("sla_pincode_alert").select("week_start").order("week_start", { ascending: false }).limit(1);
    return data?.[0]?.week_start || null;
  }

  async function refresh() {
    const w = await resolveWeek();
    week.value = w;
    if (!w) { alerts.value = []; revised.value = {}; loaded.value = true; return; }
    const [a, r] = await Promise.all([
      supabase.from("sla_pincode_alert").select("*").eq("week_start", w),
      supabase.from("sla_pincode_revised").select("*").eq("week_start", w),
    ]);
    if (!a.error) alerts.value = a.data;
    if (!r.error) revised.value = Object.fromEntries(r.data.map((x) => [x.pincode, x]));
    loadError.value = a.error?.message || r.error?.message || "";
    loaded.value = true;
  }

  const isRevised = (pincode) => !!revised.value[pincode]?.revised;

  async function setRevised(pincode, value) {
    const w = week.value;
    if (!w || isRevised(pincode) === value) return;
    const prev = revised.value[pincode];
    const who = (typeof editorLabel === "function" ? editorLabel() : editorLabel) || null;
    const row = { week_start: w, pincode, revised: value, updated_by: who, updated_at: new Date().toISOString() };
    revised.value = { ...revised.value, [pincode]: row };
    const { error } = await supabase.from("sla_pincode_revised").upsert(row, { onConflict: "week_start,pincode" });
    if (error) {
      revised.value = { ...revised.value, [pincode]: prev };
      saveError.value = `Couldn't save "SLA revised" for ${pincode}: ${error.message}`;
    } else saveError.value = "";
  }

  const syncedAt = computed(() => alerts.value.reduce((t, r) => (r.synced_at > t ? r.synced_at : t), "") || null);

  watch(weekStartRef, (w, old) => { if (w && w !== old) refresh(); });
  let timer = null;
  onMounted(() => { refresh(); timer = setInterval(refresh, POLL_INTERVAL_MS); });
  onUnmounted(() => clearInterval(timer));

  return { alerts, week, loaded, loadError, saveError, isRevised, setRevised, syncedAt, refresh };
}
