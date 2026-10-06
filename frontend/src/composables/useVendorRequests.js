// Requests the team sends a vendor about one uploaded invoice -- a credit note, or a
// corrected invoice -- raised from admin PO Tracking's GRN pending stage and shown on the
// vendor's side until answered. See invoice_vendor_requests in schema.sql.
//
// "Answered" is derived from the vendor's own uploads, never stored, so a vendor never
// needs write access here: a credit_note request is answered once that invoice has a
// credit note uploaded after the request; a reupload_invoice request once any newer
// invoice has been uploaded on the same PO.
//
// Module-level singleton, same as useInvoiceUploads -- admin and vendor shells each mount
// one app, so one shared list is all either needs.
import { computed, ref } from "vue";
import { supabase } from "../supabaseClient.js";

export const REQUEST_KIND_META = {
  credit_note: { label: "Credit note requested", short: "CN requested", vendorCta: "Upload credit note" },
  reupload_invoice: { label: "Corrected invoice requested", short: "Re-upload requested", vendorCta: "Upload corrected invoice" },
};

const requests = ref([]); // status = 'active' only, newest first

// Newest active request per upload id -- a second request on the same invoice (say, the
// first credit note wasn't enough) supersedes the earlier one.
const latestByUpload = computed(() => {
  const m = {};
  for (const r of requests.value) if (!m[r.upload_id]) m[r.upload_id] = r;
  return m;
});

// uploadsOnPo: every po_invoice_uploads row known for the request's PO (needs id,
// created_at, credit_note_uploaded_at).
export function isRequestAnswered(req, uploadsOnPo) {
  const after = (t) => !!t && new Date(t) > new Date(req.requested_at);
  if (req.kind === "credit_note") {
    const u = uploadsOnPo.find((x) => x.id === req.upload_id);
    return !!u && after(u.credit_note_uploaded_at);
  }
  return uploadsOnPo.some((x) => x.id !== req.upload_id && after(x.created_at));
}

export function useVendorRequests() {
  const working = ref(false);

  async function fetchRequests(vendorCode = null) {
    let q = supabase.from("invoice_vendor_requests").select("*").eq("status", "active").order("requested_at", { ascending: false });
    if (vendorCode) q = q.eq("vendor_code", vendorCode);
    const { data, error } = await q;
    if (!error) requests.value = data || [];
    return { error };
  }

  // One request per selected invoice upload, all the same kind and note.
  async function createRequests(uploads, kind, note, requesterLabel) {
    working.value = true;
    try {
      const { data: { user } } = await supabase.auth.getUser();
      const rows = uploads.map((u) => ({
        upload_id: u.id, po_code: u.po_code, vendor_code: u.vendor_code, kind,
        note: note?.trim() || null, requested_by: user?.id, requested_by_name: requesterLabel || null,
      }));
      const { error } = await supabase.from("invoice_vendor_requests").insert(rows);
      if (error) return { ok: false, error: error.message };
      await fetchRequests();
      return { ok: true };
    } finally {
      working.value = false;
    }
  }

  async function withdrawRequest(req, withdrawerLabel) {
    working.value = true;
    try {
      const { error } = await supabase.from("invoice_vendor_requests")
        .update({ status: "withdrawn", withdrawn_at: new Date().toISOString(), withdrawn_by_name: withdrawerLabel || null })
        .eq("id", req.id);
      if (error) return { ok: false, error: error.message };
      requests.value = requests.value.filter((r) => r.id !== req.id);
      return { ok: true };
    } finally {
      working.value = false;
    }
  }

  return { requests, latestByUpload, working, fetchRequests, createRequests, withdrawRequest };
}
