// Vendor-uploaded invoice copy files for a PO (dispatch documentation --
// distinct from a GRN's own vendor_invoice_number field). Multiple files
// per PO are expected since dispatches happen in batches.
//
// Files live in the private "po-invoices" Storage bucket, path
// "<vendor_code>/<po_code>/<timestamp>-<filename>"; po_invoice_uploads
// is just the metadata row. Both are gated by the RLS policies in
// schema.sql, so this composable enforces nothing itself -- a vendor
// physically cannot upload/see/delete another vendor's files no matter
// what this code does.
//
// Every upload also gets an AI match check (checkInvoiceMatch, via the
// check-invoice-match Edge Function) firing right after it -- reads the
// PDF, compares it against the PO/GRN, and writes match_status/
// match_summary/match_details back onto the row.
//
// Module-level singleton (like usePdfDownload) so upload/delete
// in-flight state survives independently of which modal instance is
// currently mounted, keyed by po_code (and by upload row id for deletes).
import { reactive, ref } from "vue";
import { supabase } from "../supabaseClient.js";

const BUCKET = "po-invoices";
export const MAX_INVOICE_BYTES = 15 * 1024 * 1024;

// Shared by every place a file picker can trigger an invoice upload
// (the PO detail modal, the PO Tracking table's row-level button) so the
// same PDF-only / size-limit rule can't drift between them.
export function validateInvoiceFile(file) {
  if (file.type !== "application/pdf") {
    return `"${file.name}" isn't a PDF -- only PDF invoice copies can be uploaded.`;
  }
  if (file.size > MAX_INVOICE_BYTES) {
    return `"${file.name}" is larger than 15 MB -- please compress it or split the invoice into parts.`;
  }
  return null;
}

const uploadsByPo = reactive({}); // po_code -> array of rows
const allUploads = ref([]); // flat list of every upload this login can see -- for the Payment Dashboard
const loadingPo = reactive(new Set()); // po_codes currently being (re)fetched
const workingIds = reactive(new Set()); // po_code (as `upload:<po_code>`) or row id currently mid-action

export function useInvoiceUploads() {
  // Flat, unkeyed fetch for the Payment Dashboard -- every upload this
  // login can see (RLS scopes a vendor to their own, same as everywhere
  // else), independent of which PO's detail modal has been opened.
  async function fetchAllUploads() {
    const { data, error } = await supabase
      .from("po_invoice_uploads").select("*").order("created_at", { ascending: false });
    if (!error) allUploads.value = data;
    return { data, error };
  }

  // Bulk, count-only fetch for the PO Tracking table's pending-invoice
  // badge -- one query for every PO code not already known (whether from
  // a prior call here or from a modal's own fetchInvoices), instead of a
  // full per-PO detail fetch for rows nobody's opened yet. Placeholder
  // entries hold only a length; opening that PO's modal later replaces
  // them with the real rows via fetchInvoices, same reactive map either way.
  async function fetchUploadCounts(poCodes) {
    const needed = [...new Set(poCodes)].filter(code => code && !uploadsByPo[code]);
    if (!needed.length) return;
    const { data, error } = await supabase.from("po_invoice_uploads").select("po_code").in("po_code", needed);
    if (error) return;
    const countByCode = {};
    for (const row of data) countByCode[row.po_code] = (countByCode[row.po_code] || 0) + 1;
    for (const code of needed) {
      uploadsByPo[code] = new Array(countByCode[code] || 0).fill(null);
    }
  }

  async function fetchInvoices(poCode) {
    loadingPo.add(poCode);
    try {
      const { data, error } = await supabase
        .from("po_invoice_uploads")
        .select("*")
        .eq("po_code", poCode)
        .order("created_at", { ascending: false });
      if (!error) {
        uploadsByPo[poCode] = data;
        // Keep the Payment Dashboard's flat list in sync too -- every
        // upload/delete/check path already calls this function, so
        // patching it here (rather than a separate full re-fetch) means
        // a check made from the PO detail modal shows up on the Payment
        // Dashboard immediately, not just after a page reload.
        const otherPos = allUploads.value.filter((r) => r.po_code !== poCode);
        allUploads.value = [...otherPos, ...data].sort(
          (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        );
      }
      return { data, error };
    } finally {
      loadingPo.delete(poCode);
    }
  }

  async function uploadInvoice(poCode, vendorCode, file, uploaderLabel) {
    const key = `upload:${poCode}`;
    workingIds.add(key);
    try {
      const safeName = file.name.replace(/[^A-Za-z0-9_.-]/g, "_");
      const path = `${vendorCode}/${poCode}/${Date.now()}-${safeName}`;

      const { error: uploadErr } = await supabase.storage.from(BUCKET).upload(path, file);
      if (uploadErr) return { ok: false, error: uploadErr.message };

      const { data: { user } } = await supabase.auth.getUser();

      const { data: inserted, error: insertErr } = await supabase
        .from("po_invoice_uploads")
        .insert({
          po_code: poCode,
          vendor_code: vendorCode,
          storage_path: path,
          file_name: file.name,
          file_size: file.size,
          uploaded_by: user?.id || null,
          uploaded_by_name: uploaderLabel || null,
        })
        .select()
        .single();
      if (insertErr) {
        // Don't leave an orphaned file with no metadata row.
        await supabase.storage.from(BUCKET).remove([path]);
        return { ok: false, error: insertErr.message };
      }

      await fetchInvoices(poCode);
      // Fire the AI match check right away -- don't block the upload UI
      // on it finishing; the row shows "Checking…" (match_status
      // defaults to 'pending') until this resolves and refreshes the list.
      checkInvoiceMatch(poCode, inserted.id);
      return { ok: true };
    } finally {
      workingIds.delete(key);
    }
  }

  async function checkInvoiceMatch(poCode, uploadId) {
    const key = `check:${uploadId}`;
    workingIds.add(key);
    try {
      const { data, error } = await supabase.functions.invoke("check-invoice-match", {
        body: { upload_id: uploadId },
      });
      if (error || data?.error) {
        let detail = data?.error || error?.message || "unexpected response from server";
        if (error?.context?.json) {
          try {
            const errBody = await error.context.json();
            if (errBody?.error) detail = errBody.error;
          } catch { /* body wasn't JSON -- keep whatever we already had */ }
        }
        return { ok: false, error: detail };
      }
      await fetchInvoices(poCode);
      return { ok: true, result: data };
    } finally {
      workingIds.delete(key);
    }
  }

  async function deleteInvoice(row) {
    workingIds.add(row.id);
    try {
      await supabase.storage.from(BUCKET).remove([row.storage_path]);
      const { error } = await supabase.from("po_invoice_uploads").delete().eq("id", row.id);
      if (error) return { ok: false, error: error.message };
      await fetchInvoices(row.po_code);
      return { ok: true };
    } finally {
      workingIds.delete(row.id);
    }
  }

  // Bucket is private, so viewing requires a short-lived signed URL --
  // opened via a real anchor click (not window.open) so it isn't treated
  // as an unsolicited popup.
  async function viewInvoice(row) {
    const { data, error } = await supabase.storage.from(BUCKET).createSignedUrl(row.storage_path, 120);
    if (error || !data) {
      alert(`Couldn't open file: ${error?.message || "unknown error"}`);
      return;
    }
    const a = document.createElement("a");
    a.href = data.signedUrl;
    a.target = "_blank";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  return {
    uploadsByPo, allUploads, loadingPo, workingIds,
    fetchInvoices, fetchUploadCounts, fetchAllUploads, uploadInvoice, deleteInvoice, viewInvoice, checkInvoiceMatch,
  };
}
