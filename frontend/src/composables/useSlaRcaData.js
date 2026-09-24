// SLA › Week-N RCA. Reads the newest sla_rca_run row. Its payload is the full
// /late-delivery-rca view model (parse_late_delivery_rca.js --portal-json) for the week
// the sync chose by the display rule: Wed–Sun shows the current week, Mon/Tue the
// previous one. Written from a VPN machine by sync_sla_portal.js, which keeps only the
// newest 8 runs.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useSlaRcaData() {
  const run = ref(null);
  const loadError = ref("");
  const loaded = ref(false);

  async function refresh() {
    // Probe the newest id first, so the ~200 KB payload is only re-downloaded when a new run lands.
    const { data: head, error: e1 } = await supabase
      .from("sla_rca_run").select("id").order("generated_at", { ascending: false }).limit(1);
    if (e1) { loadError.value = e1.message; loaded.value = true; return; }
    const latestId = head?.[0]?.id;
    if (!latestId) { run.value = null; loaded.value = true; return; }
    if (run.value?.id === latestId) return;
    const { data, error } = await supabase.from("sla_rca_run").select("*").eq("id", latestId).single();
    if (error) { loadError.value = error.message; loaded.value = true; return; }
    loadError.value = "";
    run.value = data;
    loaded.value = true;
  }

  let timer = null;
  onMounted(() => { refresh(); timer = setInterval(refresh, POLL_INTERVAL_MS); });
  onUnmounted(() => clearInterval(timer));

  return { run, loaded, loadError, refresh };
}
