// Requests the team sends a vendor about one uploaded invoice -- a credit note, or a
// corrected invoice -- raised from admin PO Tracking's GRN pending stage and shown on the
// vendor's side until answered. See invoice_vendor_requests in schema.sql.
//
// "Answered" is derived from the vendor's own uploads, never stored, so a vendor never
// needs write access here: a credit_note request is answered once that invoice has a
// credit note uploaded after the request; a reupload_invoice request once any newer
// invoice has been uploaded on the same PO; a dummy_po_invoice request (the team raised an
// extra "dummy" PO in Uniware to GRN quantity the original PO couldn't hold -- e.g.
// PUHY/PO2627/0562 for the 4 units over PUHY/PO2627/0432's 116) once that dummy PO
// (target_po_code) has any invoice uploaded at all -- before or after the request, since the
// vendor often uploads it as soon as the dummy PO reaches them, and asking twice would be wrong.
//
// Module-level singleton, same as useInvoiceUploads -- admin and vendor shells each mount
// one app, so one shared list is all either needs.
import { computed, ref } from "vue";
import { supabase } from "../supabaseClient.js";

export const REQUEST_KIND_META = {
  credit_note: { label: "Credit note requested", short: "CN requested", vendorCta: "Upload credit note" },
  reupload_invoice: { label: "Corrected invoice requested", short: "Re-upload requested", vendorCta: "Upload corrected invoice" },
  dummy_po_invoice: { label: "Invoice needed on a new PO", short: "Dummy PO raised", vendorCta: "Upload invoice" },
};
// The vendor's button text -- names the dummy PO, since the upload goes there, not on this invoice's PO.
export function vendorCtaFor(req) {
  const base = REQUEST_KIND_META[req.kind]?.vendorCta || "Respond";
  return req.kind === "dummy_po_invoice" && req.target_po_code ? `${base} for ${req.target_po_code}` : base;
}
// Which PO an invoice-upload answer goes on.
export const answerPoCode = (req, upload) => (req.kind === "dummy_po_invoice" ? req.target_po_code : upload.po_code);

const requests = ref([]); // status = 'active' only, newest first

// Newest active request per upload id -- a second request on the same invoice (say, the
// first credit note wasn't enough) supersedes the earlier one.
const latestByUpload = computed(() => {
  const m = {};
  for (const r of requests.value) if (!m[r.upload_id]) m[r.upload_id] = r;
  return m;
});

// uploadsFor(poCode): every po_invoice_uploads row known for that PO (needs id, created_at,
// credit_note_uploaded_at) -- a function, since a dummy_po_invoice request is answered on
// another PO than the one it was raised on.
export function isRequestAnswered(req, uploadsFor) {
  const after = (t) => !!t && new Date(t) > new Date(req.requested_at);
  if (req.kind === "credit_note") {
    const u = uploadsFor(req.po_code).find((x) => x.id === req.upload_id);
    return !!u && after(u.credit_note_uploaded_at);
  }
  if (req.kind === "dummy_po_invoice") {
    return !!req.target_po_code && uploadsFor(req.target_po_code).length > 0;
  }
  return uploadsFor(req.po_code).some((x) => x.id !== req.upload_id && after(x.created_at));
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

  // One request per selected invoice upload, all the same kind and note (and, for
  // dummy_po_invoice, the same dummy PO).
  async function createRequests(uploads, kind, note, requesterLabel, targetPoCode = null) {
    working.value = true;
    try {
      const { data: { user } } = await supabase.auth.getUser();
      const rows = uploads.map((u) => ({
        upload_id: u.id, po_code: u.po_code, vendor_code: u.vendor_code, kind,
        note: note?.trim() || null, requested_by: user?.id, requested_by_name: requesterLabel || null,
        ...(kind === "dummy_po_invoice" ? { target_po_code: targetPoCode } : {}),
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
