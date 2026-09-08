// Vendor WhatsApp contact numbers (public.vendor_contacts) -- hand-
// maintained mapping of rate-card vendor name -> phone number, used by
// Rate Finder's "Send Intent" button. Writes go through
// manage-vendor-contacts (service_role), same pattern as useVendors.js.
//
// Polls every 60s like useRateCard.js -- so another internal-staff login
// editing a number shows up here without needing a page reload.
import { ref, onMounted, onUnmounted } from "vue";
import { supabase } from "../supabaseClient.js";
import { resolveFunctionError } from "../functionError.js";

const POLL_INTERVAL_MS = 60 * 1000;

export function useVendorContacts() {
  const contacts = ref([]);

  async function refresh() {
    const { data, error } = await supabase.from("vendor_contacts").select("*").order("vendor_name");
    if (!error) contacts.value = data;
    return { data, error };
  }

  function contactFor(vendorName) {
    return contacts.value.find(c => c.vendor_name === vendorName) || null;
  }

  async function callContactAction(body) {
    const { data, error } = await supabase.functions.invoke("manage-vendor-contacts", { body });
    if (error || data?.error) {
      return { ok: false, error: await resolveFunctionError(data, error) };
    }
    await refresh();
    return { ok: true };
  }

  const saveContact = (vendorName, whatsappNumber, contactName) =>
    callContactAction({ action: "upsert", vendor_name: vendorName, whatsapp_number: whatsappNumber, contact_name: contactName });
  const deleteContact = (vendorName) => callContactAction({ action: "delete", vendor_name: vendorName });

  let intervalId = null;
  onMounted(async () => {
    await refresh();
    intervalId = setInterval(refresh, POLL_INTERVAL_MS);
  });
  onUnmounted(() => {
    if (intervalId) clearInterval(intervalId);
  });

  return { contacts, refresh, contactFor, saveContact, deleteContact };
}
