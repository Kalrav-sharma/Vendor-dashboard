// Admin-only convenience: lets a real admin flip between the Vendor and
// Management app shells from the Profile menu, without a separate vendor
// login, so both UIs are easy to build/test. Purely a client-side shell
// switch -- it never changes what Postgres RLS lets the session read (RLS
// is the real authorization boundary, see CLAUDE.md). VendorApp.vue only
// ever honors this after confirming ctx.profile.role === "admin" from a
// fresh DB read on that page load, so a non-admin editing localStorage
// gets nothing -- the gate still checks the real role first.
const VIEW_KEY = "admin-view-override";

// Which vendor_code to preview once "vendor" is chosen above -- otherwise
// an admin's own RLS access spans every vendor, so the Vendor shell would
// show every vendor's data mixed together instead of one vendor's actual
// view. Composables filter to this code themselves (usePurchaseOrders.js,
// useShipmentTracking.js, useInvoiceUploads.js) -- it's an extra WHERE
// clause on top of RLS, never a replacement for it, so a non-admin
// tampering with this value can only ever narrow their own RLS-limited
// rows further (down to nothing), never see more than RLS already allows.
const VENDOR_CODE_KEY = "admin-preview-vendor-code";

export function getViewOverride() {
  try {
    return localStorage.getItem(VIEW_KEY);
  } catch {
    return null;
  }
}

export function setViewOverride(view) {
  try {
    if (view) localStorage.setItem(VIEW_KEY, view);
    else localStorage.removeItem(VIEW_KEY);
  } catch {
    // Storage unavailable -- the caller navigates regardless; worst case
    // the override just doesn't stick past this one page load.
  }
}

export function getPreviewVendorCode() {
  try {
    return localStorage.getItem(VENDOR_CODE_KEY);
  } catch {
    return null;
  }
}

export function setPreviewVendorCode(code) {
  try {
    if (code) localStorage.setItem(VENDOR_CODE_KEY, code);
    else localStorage.removeItem(VENDOR_CODE_KEY);
  } catch {
    // See setViewOverride -- same non-fatal storage failure.
  }
}

// Clears both flags together so they can never point at different states
// (e.g. "vendor" view with no vendor code, or a stale code left over after
// switching back to Management). Call this on sign-out too, so a leftover
// preview from one admin's session can never affect whoever logs in next
// on the same browser.
export function clearViewOverride() {
  setViewOverride(null);
  setPreviewVendorCode(null);
}
