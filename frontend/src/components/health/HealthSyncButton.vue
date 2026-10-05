<script setup>
// "Sync now" on the Logistics Health Card (2026-10-05, per Anish): GitHub's scheduled
// inventory syncs get throttled for hours, so any internal user can start them on demand.
// Calls the trigger-inventory-sync Edge Function, which dispatches the RO (Uniware -> S&OP
// inventory) and Spares syncs, then polls it until all three runs finish and emits "done"
// so the card reloads its data instead of waiting for the 5-min poll.
import { ref, computed, onMounted, onUnmounted } from "vue";
import { supabase } from "../../supabaseClient.js";

const emit = defineEmits(["done"]);

const FN = "trigger-inventory-sync";
const POLL_MS = 15 * 1000;
const GIVE_UP_MS = 12 * 60 * 1000;
const STORE_KEY = "hc-sync-since"; // keeps an in-flight sync showing across a reload
const LABELS = {
  "sync-uniware-inventory.yml": "RO stock",
  "sync-sop-inventory.yml": "RO tables",
  "sync-spares.yml": "Spares",
};

const state = ref("idle"); // idle | running | done | failed | timeout
const message = ref("");
const failedUrl = ref("");
let since = null;
let timer = null;

const busy = computed(() => state.value === "running");
const buttonText = computed(() => (busy.value ? "Syncing RO + Spares…" : "Sync now"));

async function call(body) {
  const { data, error } = await supabase.functions.invoke(FN, { body });
  if (!error) return data;
  // Non-2xx: the function's own { error } message is more useful than "non-2xx status".
  let msg = error.message;
  try {
    const b = await error.context.json();
    if (b?.error) msg = b.error;
  } catch { /* not a JSON body -- keep the generic message */ }
  throw new Error(msg);
}

function remember(v) {
  try {
    if (v) sessionStorage.setItem(STORE_KEY, v);
    else sessionStorage.removeItem(STORE_KEY);
  } catch { /* storage blocked -- the sync still runs, it just won't survive a reload */ }
}

function stop(next, msg = "") {
  clearInterval(timer);
  timer = null;
  state.value = next;
  message.value = msg;
  remember(null);
}

async function poll() {
  if (Date.now() - Date.parse(since) > GIVE_UP_MS) {
    stop("timeout", "Still running on GitHub — numbers will update when it finishes.");
    return;
  }
  let runs;
  try {
    ({ runs } = await call({ action: "status", since }));
  } catch (e) {
    stop("failed", e.message);
    return;
  }
  const failed = runs.find((r) => r.status === "completed" && r.conclusion !== "success");
  if (failed) {
    failedUrl.value = failed.html_url;
    stop("failed", `${LABELS[failed.wf] || failed.wf} sync failed`);
    emit("done"); // whatever did finish is still worth showing
    return;
  }
  const pending = runs.filter((r) => r.status !== "completed").map((r) => LABELS[r.wf] || r.wf);
  if (pending.length) {
    message.value = `Waiting on ${pending.join(", ")}`;
    return;
  }
  stop("done", "Synced ✓");
  emit("done");
}

function startPolling() {
  state.value = "running";
  failedUrl.value = "";
  timer = setInterval(poll, POLL_MS);
  poll();
}

async function syncNow() {
  if (busy.value) return;
  state.value = "running";
  failedUrl.value = "";
  message.value = "Starting…";
  try {
    const res = await call({ action: "trigger" });
    since = res.since;
    remember(since);
    startPolling();
  } catch (e) {
    stop("failed", e.message);
  }
}

onMounted(() => {
  let saved = null;
  try { saved = sessionStorage.getItem(STORE_KEY); } catch { /* storage blocked */ }
  if (saved && Date.now() - Date.parse(saved) < GIVE_UP_MS) {
    since = saved;
    startPolling();
  }
});
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <div class="hc-sync">
    <button type="button" class="hc-sync-btn" :disabled="busy" @click="syncNow">{{ buttonText }}</button>
    <em v-if="message" class="hc-sync-msg" :class="state">{{ message }}<a v-if="failedUrl" :href="failedUrl" target="_blank" rel="noopener"> · details</a></em>
  </div>
</template>
