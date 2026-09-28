// Admin-only convenience: lets a real admin flip between the Vendor and
// Management app shells from the Profile menu, without a separate vendor
// login, so both UIs are easy to build/test. Purely a client-side shell
// switch -- it never changes what Postgres RLS lets the session read (RLS
// is the real authorization boundary, see CLAUDE.md). VendorApp.vue only
// ever honors this after confirming ctx.profile.role === "admin" from a
// fresh DB read on that page load, so a non-admin editing localStorage
// gets nothing -- the gate still checks the real role first.
const KEY = "admin-view-override";

export function getViewOverride() {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setViewOverride(view) {
  try {
    if (view) localStorage.setItem(KEY, view);
    else localStorage.removeItem(KEY);
  } catch {
    // Storage unavailable -- the caller navigates regardless; worst case
    // the override just doesn't stick past this one page load.
  }
}
