// Ops -> Finance handoffs for short-GRN'd invoices (grn_finance_handoffs in schema.sql):
// admin PO Tracking › GRN pending › Change stage › "Move to Finance" creates one; Finance's
// Action Required › From Ops · GRN tab approves it (invoice joins Ready to book) or returns
// it with a comment (PO goes back to GRN pending, showing the comment). Staff-only -- the
// vendor never sees these. The newest row per upload is the one that counts.
//
// Module-level singleton, same as useVendorRequests / useInvoiceUploads.
import { computed, ref } from "vue";
import { supabase } from "../supabaseClient.js";

const handoffs = ref([]); // newest first

const latestByUpload = computed(() => {
  const m = {};
  for (const h of handoffs.value) if (!m[h.upload_id]) m[h.upload_id] = h;
  return m;
});

export function useGrnHandoffs() {
  const working = ref(false);

  async function fetchHandoffs() {
    const { data, error } = await supabase.from("grn_finance_handoffs").select("*").order("sent_at", { ascending: false });
    if (!error) handoffs.value = data || [];
    return { error };
  }

  // One handoff per selected invoice upload, all with the same note.
  async function sendToFinance(uploads, note, senderLabel) {
    working.value = true;
    try {
      const { data: { user } } = await supabase.auth.getUser();
      const rows = uploads.map((u) => ({
        upload_id: u.id, po_code: u.po_code, vendor_code: u.vendor_code,
        ops_note: note?.trim() || null, sent_by: user?.id, sent_by_name: senderLabel || null,
      }));
      const { error } = await supabase.from("grn_finance_handoffs").insert(rows);
      if (error) return { ok: false, error: error.message };
      await fetchHandoffs();
      return { ok: true };
    } finally {
      working.value = false;
    }
  }

  // Finance's call: status "approved" (proceed with payment) or "returned" (back to GRN pending).
  async function decideHandoff(handoff, status, comment, deciderLabel) {
    working.value = true;
    try {
      const { data: { user } } = await supabase.auth.getUser();
      const { error } = await supabase.from("grn_finance_handoffs").update({
        status, finance_comment: comment?.trim() || null,
        decided_by: user?.id, decided_by_name: deciderLabel || null, decided_at: new Date().toISOString(),
      }).eq("id", handoff.id);
      if (error) return { ok: false, error: error.message };
      await fetchHandoffs();
      return { ok: true };
    } finally {
      working.value = false;
    }
  }

  return { handoffs, latestByUpload, working, fetchHandoffs, sendToFinance, decideHandoff };
}
