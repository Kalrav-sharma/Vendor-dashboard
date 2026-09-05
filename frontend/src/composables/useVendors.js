// Vendor login accounts (profiles.role='vendor') -- admin-only. Ported
// from loadVendors()/vendorLabel() in the legacy docs/admin.html.
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

  return { vendors, refresh, vendorLabel };
}
