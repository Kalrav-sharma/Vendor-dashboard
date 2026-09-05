// Calls the get-po-pdf Edge Function, which fetches the actual official
// Uniware document (not something we generate) and streams it back --
// authorization is enforced by the same RLS every other query relies on,
// so this can only ever return a PO the caller is allowed to see.
//
// Ported from downloadPoPdf() in the legacy docs/assets/app-common.js.
// downloadingCodes lets each row's button independently show its own
// "Fetching…" state without a per-row component instance managing that.
import { reactive } from "vue";
import { supabase } from "../supabaseClient.js";

const downloadingCodes = reactive(new Set());

export function usePdfDownload() {
  async function downloadPoPdf(poCode) {
    downloadingCodes.add(poCode);
    try {
      const { data, error } = await supabase.functions.invoke(
        `get-po-pdf?po_code=${encodeURIComponent(poCode)}`,
        { method: "GET" }
      );
      if (error || !(data instanceof Blob)) {
        // supabase-js's error.message is a generic "non-2xx status code" --
        // the actual reason is in the response body the function returned.
        let detail = error?.message || "unexpected response from server";
        if (error?.context?.json) {
          try {
            const body = await error.context.json();
            if (body?.error) detail = body.error;
          } catch { /* body wasn't JSON -- keep the generic message */ }
        }
        alert(`Couldn't fetch the PO PDF: ${detail}`);
        return;
      }
      const blobUrl = URL.createObjectURL(data);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = `PO_${poCode.replace(/[^A-Za-z0-9_-]/g, "_")}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (e) {
      alert(`Couldn't fetch the PO PDF: ${e.message || e}`);
    } finally {
      downloadingCodes.delete(poCode);
    }
  }

  return { downloadingCodes, downloadPoPdf };
}
