import { onMounted, onUnmounted } from "vue";

const visibilityListeners = new Map();

// Like setInterval, but skips ticks while the browser tab is hidden (one catch-up refresh when shown again) and never overlaps a still-running refresh.
export function setVisibleInterval(fn, ms) {
  let missed = false;
  let inFlight = false;

  async function run() {
    if (inFlight) return;
    inFlight = true;
    try {
      await fn();
    } finally {
      inFlight = false;
    }
  }

  const id = setInterval(() => {
    if (document.hidden) {
      missed = true;
      return;
    }
    run();
  }, ms);

  const onVisibilityChange = () => {
    if (!document.hidden && missed) {
      missed = false;
      run();
    }
  };
  document.addEventListener("visibilitychange", onVisibilityChange);
  visibilityListeners.set(id, onVisibilityChange);
  return id;
}

export function clearVisibleInterval(id) {
  clearInterval(id);
  const listener = visibilityListeners.get(id);
  if (listener) {
    document.removeEventListener("visibilitychange", listener);
    visibilityListeners.delete(id);
  }
}

// One fetch + one poll shared by every mounted caller of a parameterless composable; `shared` is module-level { subscribers: 0, intervalId: null }.
export function useSharedPoll(shared, refresh, intervalMs) {
  onMounted(async () => {
    shared.subscribers += 1;
    if (shared.subscribers !== 1) return;
    await refresh();
    if (shared.subscribers > 0 && !shared.intervalId) {
      shared.intervalId = setVisibleInterval(refresh, intervalMs);
    }
  });
  onUnmounted(() => {
    shared.subscribers -= 1;
    if (shared.subscribers === 0 && shared.intervalId) {
      clearVisibleInterval(shared.intervalId);
      shared.intervalId = null;
    }
  });
}
