// Vendor-raised support tickets (Raise a Ticket screen) -- written
// directly by the app through RLS (support_tickets in schema.sql), same
// discipline as po_invoice_uploads. A vendor sees only their own tickets;
// internal staff see every vendor's, via the admin console's Tickets
// screen, and are the only ones who can update status/leave a response.
import { ref } from "vue";
import { supabase } from "../supabaseClient.js";

export function useSupportTickets() {
  const tickets = ref([]);
  const loading = ref(false);

  // `vendorCode`: admin-only "preview as vendor" support, same pattern as
  // usePurchaseOrders.js -- a real vendor login never passes this; RLS
  // alone already scopes them to their own vendor_code.
  async function fetchTickets(vendorCode = null) {
    loading.value = true;
    try {
      let query = supabase.from("support_tickets").select("*").order("created_at", { ascending: false });
      if (vendorCode) query = query.eq("vendor_code", vendorCode);
      const { data, error } = await query;
      if (!error) tickets.value = data;
      return { data, error };
    } finally {
      loading.value = false;
    }
  }

  async function raiseTicket({ vendorCode, vendorName, category, poCode, subject, description, createdByName }) {
    const { data: { user } } = await supabase.auth.getUser();
    const { data, error } = await supabase
      .from("support_tickets")
      .insert({
        vendor_code: vendorCode,
        vendor_name: vendorName || null,
        category,
        po_code: poCode || null,
        subject,
        description,
        created_by: user?.id || null,
        created_by_name: createdByName || null,
      })
      .select()
      .single();
    if (!error) tickets.value = [data, ...tickets.value];
    return { data, error };
  }

  // Internal-staff-only, per the support_tickets_update RLS policy.
  async function updateTicket(id, { status, adminResponse, respondedByName }) {
    const patch = { updated_at: new Date().toISOString() };
    if (status !== undefined) patch.status = status;
    if (adminResponse !== undefined) patch.admin_response = adminResponse;
    if (respondedByName !== undefined) patch.responded_by_name = respondedByName;
    if (status === "resolved") patch.resolved_at = new Date().toISOString();
    const { data, error } = await supabase.from("support_tickets").update(patch).eq("id", id).select().single();
    if (!error) tickets.value = tickets.value.map((t) => (t.id === id ? data : t));
    return { data, error };
  }

  return { tickets, loading, fetchTickets, raiseTicket, updateTicket };
}
