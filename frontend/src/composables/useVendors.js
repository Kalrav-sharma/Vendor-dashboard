// Vendor login accounts (profiles.role='vendor') -- admin-only. Ported
// from loadVendors()/vendorLabel() in the legacy docs/admin.html, plus
// revoke/restore/delete, which all call the admin-create-vendor Edge
// Function (real server-side enforcement via a Supabase Auth ban, not a
// hidden UI button -- see that function's comments).
import { ref } from "vue";
import { supabase } from "../supabaseClient.js";

export function useVendors() {
  const vendors = ref([]);

  async function refresh() {
    const { data, error } = await supabase
      .from("profiles").select("*").eq("role", "vendor").order("created_at", { ascending: false });
    if (!error) vendors.value = data;
    return { data, error };
  }

  // Prefers the vendor_name a PO/SKU row already carries (works even if no
  // login has been created for that vendor yet); falls back to the
  // profiles-based lookup, then the raw code.
  function vendorLabel(code, rowName) {
    if (rowName) return rowName;
    const found = vendors.value.find(v => v.vendor_code === code);
    return found?.vendor_name || code || "";
  }

  async function callVendorAction(action, userId) {
    const { data, error } = await supabase.functions.invoke("admin-create-vendor", {
      body: { action, user_id: userId },
    });
    if (error || data?.error) {
      let detail = data?.error || error?.message || "unexpected response from server";
      if (error?.context?.json) {
        try {
          const body = await error.context.json();
          if (body?.error) detail = body.error;
        } catch { /* body wasn't JSON -- keep whatever we already had */ }
      }
      return { ok: false, error: detail };
    }
    await refresh();
    return { ok: true };
  }

  const revokeVendor = (userId) => callVendorAction("revoke", userId);
  const restoreVendor = (userId) => callVendorAction("restore", userId);
  const deleteVendor = (userId) => callVendorAction("delete", userId);

  return { vendors, refresh, vendorLabel, revokeVendor, restoreVendor, deleteVendor };
}
