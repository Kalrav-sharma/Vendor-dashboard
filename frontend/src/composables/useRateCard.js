// Mid Mile rate card (public.mm_rate_card) -- synced from the "Native -
// Commercials" sheet by scripts/sync_mm_rate_card.py (see that script's
// docstring). Powers Rate Finder's "cheapest vendor for this lane" lookup.
import { ref, computed } from "vue";
import { supabase } from "../supabaseClient.js";

function normalize(s) {
  return String(s || "").trim().replace(/\s+/g, " ").toUpperCase();
}

function laneKey(origin, destination, truckSize) {
  return [origin, destination, truckSize].map(normalize).join("|");
}

// Dedupes a list of raw display strings case/whitespace-insensitively,
// keeping the first-seen casing (the sheet isn't always consistent --
// e.g. "Bangalore" in one row, "BANGALORE" in another).
function dedupeDisplay(values) {
  const byKey = new Map();
  for (const v of values) {
    const k = normalize(v);
    if (k && !byKey.has(k)) byKey.set(k, v);
  }
  return [...byKey.values()].sort((a, b) => a.localeCompare(b));
}

export function useRateCard() {
  const rows = ref([]);

  async function refresh() {
    const { data, error } = await supabase.from("mm_rate_card").select("*");
    if (!error) rows.value = data;
    return { data, error };
  }

  const origins = computed(() => dedupeDisplay(rows.value.map(r => r.origin)));
  const destinations = computed(() => dedupeDisplay(rows.value.map(r => r.destination)));
  const truckSizes = computed(() => dedupeDisplay(rows.value.map(r => r.truck_size)));

  // (origin, destination, truckSize) => matching row | null
  function findLane(origin, destination, truckSize) {
    const key = laneKey(origin, destination, truckSize);
    return rows.value.find(r => r.lane_key === key) || null;
  }

  return { rows, refresh, origins, destinations, truckSizes, findLane };
}
