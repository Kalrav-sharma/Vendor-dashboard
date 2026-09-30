// Keeps the sidebar's active tab in the URL hash (e.g. "#dispatch-planning")
// so refreshing the page, or reopening a bookmarked/shared link, lands back
// on the same tab instead of always resetting to the default one. Shared by
// VendorApp.vue and AdminApp.vue's own activeNav.
export function readNavHash() {
  return (window.location.hash || "").slice(1) || null;
}

// replaceState, not `location.hash = id` -- a plain hash assignment pushes
// a new browser-history entry per tab switch, so the back button would
// step through every tab visited instead of leaving the page normally.
export function writeNavHash(id) {
  if (!id) return;
  history.replaceState(null, "", "#" + id);
}
