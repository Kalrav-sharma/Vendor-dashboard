// Manual light/dark override for the OS-driven default already in shared.css
// (every `@media (prefers-color-scheme: dark)` block there and in the SLA/
// Health Card CSS). Stored as "system" | "light" | "dark" -- "system" means
// no override, so a user who never touches this keeps today's behaviour
// exactly: the OS preference decides, via those same media queries.
const KEY = "theme-preference";

export function getTheme() {
  try {
    return localStorage.getItem(KEY) || "system";
  } catch {
    return "system";
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
  else root.removeAttribute("data-theme");
}

export function setTheme(theme) {
  try {
    if (theme === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    // Storage unavailable (e.g. locked-down browser profile) -- the theme
    // still applies for this page load, it just won't persist.
  }
  applyTheme(theme);
  // Chart.js canvases read CSS custom properties at draw time, not
  // reactively -- SlaChart.vue listens for this to redraw on a manual
  // switch, the same way it already redraws on the OS-level media query.
  window.dispatchEvent(new CustomEvent("themechange"));
}

// Call once, as early as possible on every page (see supabaseClient.js,
// imported by all of them), so the stored preference is set before first
// paint instead of flashing the OS default and then switching.
export function initTheme() {
  applyTheme(getTheme());
}
