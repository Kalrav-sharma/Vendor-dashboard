// AI-assisted invoice-vs-PO/GRN match check. Runs automatically right
// after a vendor (or admin) uploads an invoice PDF (see uploadInvoice()
// in useInvoiceUploads.js), and can be re-run manually via the
// "Re-check" button -- useful once a GRN shows up later in Uniware for
// an invoice that was originally checked against the PO alone.
//
// Design: Claude only does the OCR/extraction (reading the PDF's
// invoice number/date/due date/PO reference/total, plus each line's printed
// quantity purely so the total can be summed), forced via tool_choice
// into one strict tool call so the output is always structured. All
// actual matching logic is plain deterministic TypeScript below -- not
// something the model is asked to judge. That keeps the comparison
// auditable and doesn't depend on an LLM's arithmetic being right.
//
// Deliberately a HEADER-LEVEL 3-way check, not a SKU-by-SKU one -- real
// vendor invoices routinely don't print SKU codes at all (just
// descriptions), which made a line-by-line SKU match produce dozens of
// false "unmatched line" discrepancies on real invoices. The five checks:
//   1. GRN received value vs invoice total
//   2. Invoice number vs the GRN's own recorded invoice number (this is
//      also how the matching GRN is selected in the first place)
//   3. GRN received qty vs invoice qty (summed from its line quantities)
//   4. Invoice qty/value doesn't exceed this PO's own ordered qty/value
//   5. The PO number printed on the invoice matches this PO's code
// Checks 1 and 3 only run once a GRN is actually found by invoice number
// (see below) -- without one there's no confirmed receipt to compare
// against, so the result is "needs_review" rather than a hard pass/fail.
//
// Required secrets (Project Settings -> Edge Functions -> Secrets):
//   ANTHROPIC_API_KEY  -- pay-as-you-go key from console.anthropic.com,
//                         separate from any Claude.ai/Claude Code login
// (SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY are the
// same ones every other function in this project already uses.)
//
// Deploy with: supabase functions deploy check-invoice-match

import { createClient } from "npm:@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY")!;
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const ANTHROPIC_API_KEY = Deno.env.get("ANTHROPIC_API_KEY")!;

// Cheapest capable model -- this is straightforward document extraction,
// not something that needs frontier-level reasoning.
const ANTHROPIC_MODEL = "claude-haiku-4-5-20251001";
const BUCKET = "po-invoices";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS_HEADERS });
  }

  try {
    const authHeader = req.headers.get("Authorization");
    if (!authHeader) {
      return json({ error: "Missing Authorization header" }, 401);
    }

    // Scoped to the caller's own JWT -- RLS decides what they can see.
    const callerClient = createClient(SUPABASE_URL, ANON_KEY, {
      global: { headers: { Authorization: authHeader } },
    });
    const { data: { user }, error: userErr } = await callerClient.auth.getUser();
    if (userErr || !user) {
      return json({ error: "Not authenticated" }, 401);
    }

    const body = await req.json();
    const uploadId = body?.upload_id;
    if (!uploadId) {
      return json({ error: "upload_id is required" }, 400);
    }

    // Reading this row through the caller's own RLS-scoped client IS the
    // authorization check -- if they can't see it (wrong vendor_code,
    // not an admin), this returns nothing and we refuse below. No
    // hand-rolled role check needed on top of that.
    const { data: upload, error: uploadErr } = await callerClient
      .from("po_invoice_uploads").select("*").eq("id", uploadId).single();
    if (uploadErr || !upload) {
      return json({ error: "Invoice upload not found or not accessible" }, 404);
    }

    // Everything from here on needs service_role: downloading the
    // private file, and reading the PO/grns/grn_items regardless of
    // whose vendor_code they belong to (already authorized above).
    const adminClient = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);

    const { data: fileBlob, error: downloadErr } = await adminClient.storage.from(BUCKET).download(upload.storage_path);
    if (downloadErr || !fileBlob) {
      return await recordResult(adminClient, upload.id, "error", "Couldn't read the uploaded file back from storage.", null);
    }

    const [{ data: po }, { data: grns }] = await Promise.all([
      adminClient.from("purchase_orders").select("po_code, total_amount, qty_ordered").eq("po_code", upload.po_code).single(),
      adminClient.from("grns").select("*").eq("po_code", upload.po_code),
    ]);
    const grnsList = grns || [];

    let extracted;
    try {
      const pdfBase64 = arrayBufferToBase64(await fileBlob.arrayBuffer());
      extracted = await extractInvoiceData(pdfBase64);
    } catch (e) {
      return await recordResult(
        adminClient, upload.id, "error",
        `Couldn't read the invoice PDF: ${e instanceof Error ? e.message : String(e)}`, null
      );
    }

    const matchingGrn = grnsList.find((g) => {
      const gNo = normalizeCode(g.vendor_invoice_number);
      return gNo && gNo === normalizeCode(extracted.invoice_number);
    });

    let grnItems: any[] = [];
    if (matchingGrn) {
      const { data } = await adminClient
        .from("grn_items").select("quantity").eq("grn_code", matchingGrn.grn_code);
      grnItems = data || [];
    }

    const { status, summary, discrepancies, referenceLabel } = compareInvoiceToReference(
      extracted, po, matchingGrn, grnItems, grnsList
    );

    return await recordResult(adminClient, upload.id, status, summary, {
      extracted, reference: referenceLabel, discrepancies,
      // Explicit, easy-to-read fields for the Payment Dashboard -- pulled
      // from the same data already used for the comparison above, so
      // there's no second source of truth to drift out of sync.
      invoice_value: Number.isFinite(extracted.grand_total) && extracted.grand_total >= 0 ? Number(extracted.grand_total) : null,
      invoice_due_date: extracted.invoice_due_date || null,
      grn_value: matchingGrn?.total_received_amount ?? null,
      grn_code: matchingGrn?.grn_code ?? null,
      po_value: po?.total_amount ?? null,
    });
  } catch (e) {
    return json({ error: `Unexpected error: ${e instanceof Error ? e.message : String(e)}` }, 500);
  }
});

async function extractInvoiceData(pdfBase64: string) {
  const tool = {
    name: "report_invoice_extraction",
    description: "Report the structured data read off the invoice PDF.",
    input_schema: {
      type: "object",
      properties: {
        invoice_number: { type: "string", description: "Invoice/document number as printed. Empty string if not found." },
        invoice_date: { type: "string", description: "Invoice date as ISO YYYY-MM-DD if determinable, else empty string." },
        invoice_due_date: {
          type: "string",
          description: "Payment due date as ISO YYYY-MM-DD if printed (e.g. \"Due Date\", \"Payment Due\") or determinable from printed payment terms (e.g. \"Net 30\" from the invoice date). Empty string if neither is present.",
        },
        po_number_on_invoice: {
          type: "string",
          description: "The purchase order number/reference printed on the invoice (e.g. \"PO2627/0416\" or a full PO code), empty string if the invoice doesn't reference one.",
        },
        grand_total: { type: "number", description: "Invoice grand total amount. Use -1 if not determinable." },
        line_quantities: {
          type: "array",
          items: { type: "number" },
          description: "The quantity printed on each line item, in the same order as printed -- used only to add up a total quantity, not matched against any SKU list.",
        },
        extraction_confidence: { type: "string", enum: ["high", "medium", "low"] },
      },
      required: ["invoice_number", "invoice_date", "invoice_due_date", "po_number_on_invoice", "grand_total", "line_quantities", "extraction_confidence"],
    },
  };

  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: ANTHROPIC_MODEL,
      max_tokens: 2048,
      tools: [tool],
      tool_choice: { type: "tool", name: "report_invoice_extraction" },
      messages: [{
        role: "user",
        content: [
          { type: "document", source: { type: "base64", media_type: "application/pdf", data: pdfBase64 } },
          {
            type: "text",
            text: "Read this vendor invoice PDF and extract its data via the report_invoice_extraction tool. " +
              "Read every printed quantity/total exactly -- don't guess or round.",
          },
        ],
      }],
    }),
  });

  if (!resp.ok) {
    const errBody = await resp.text();
    throw new Error(`Anthropic API error ${resp.status}: ${errBody.slice(0, 300)}`);
  }
  const data = await resp.json();
  const toolUse = (data.content || []).find((b: any) => b.type === "tool_use");
  if (!toolUse) throw new Error("Model didn't return a structured extraction.");
  return toolUse.input;
}

// Shared by invoice numbers AND PO codes -- both just need a
// punctuation/case-insensitive comparison (Uniware's "PUHY/PO2627/0416"
// vs a vendor's own "PO2627/0416" or "0416").
function normalizeCode(s: string | null | undefined) {
  return (s || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
}

// Header-level 3-way check -- see the file header comment for the five
// specific checks. Deliberately not SKU-by-SKU: real vendor invoices
// routinely skip printing SKU codes at all, which made a line-by-line
// match produce false "unmatched line" discrepancies on real invoices.
function compareInvoiceToReference(extracted: any, po: any, grn: any, grnItems: any[], allGrns: any[]) {
  const discrepancies: { type: string; detail: string }[] = [];
  // Flagged for a human to look at, but don't by themselves make this a
  // hard "mismatch" -- each reflects something the check couldn't fully
  // verify, not a confirmed discrepancy.
  const softTypes = new Set(["low_confidence", "missing_invoice_number", "missing_po_number"]);

  if (extracted.extraction_confidence === "low") {
    discrepancies.push({ type: "low_confidence", detail: "Low OCR confidence reading this PDF -- please review the file directly." });
  }

  // Check 2: invoice number vs the GRN's own recorded invoice number.
  // (This is also how `grn` was selected in the first place -- see the
  // caller.) A PO with GRNs already raised, none of which match this
  // invoice's number, is a real discrepancy worth flagging explicitly
  // rather than silently falling back to "needs_review".
  if (!extracted.invoice_number) {
    discrepancies.push({ type: "missing_invoice_number", detail: "Couldn't read an invoice number off the PDF." });
  } else if (!grn && allGrns.length > 0) {
    const knownNumbers = [...new Set(allGrns.map((g) => g.vendor_invoice_number).filter(Boolean))];
    discrepancies.push({
      type: "invoice_number_no_grn_match",
      detail: `Invoice number "${extracted.invoice_number}" doesn't match any GRN recorded for this PO` +
        (knownNumbers.length ? ` (found: ${knownNumbers.join(", ")}).` : "."),
    });
  }

  const invoiceQty = (extracted.line_quantities || []).reduce((s: number, q: number) => s + (q >= 0 ? Number(q) : 0), 0);
  const invoiceTotal = Number(extracted.grand_total);

  // Checks 1 and 3: GRN received value/qty vs invoice value/qty -- only
  // meaningful once we actually have a confirmed receipt to compare
  // against.
  if (grn) {
    const grnQty = grnItems.reduce((s, gi) => s + (Number(gi.quantity) || 0), 0);
    if (grn.total_received_amount != null && invoiceTotal >= 0) {
      const refTotal = Number(grn.total_received_amount);
      const tolerance = Math.max(refTotal * 0.02, 5);
      if (Math.abs(invoiceTotal - refTotal) > tolerance) {
        discrepancies.push({
          type: "grn_value_mismatch",
          detail: `Invoice total ₹${invoiceTotal} vs GRN ${grn.grn_code} received amount ₹${refTotal}.`,
        });
      }
    }
    if (grnQty > 0 && invoiceQty > 0 && Math.abs(invoiceQty - grnQty) > 0.01) {
      discrepancies.push({
        type: "grn_qty_mismatch",
        detail: `Invoice qty ${invoiceQty} vs GRN ${grn.grn_code} received qty ${grnQty}.`,
      });
    }
  }

  // Check 5: the PO number printed on the invoice matches this PO.
  const normInvoicePo = normalizeCode(extracted.po_number_on_invoice);
  const normActualPo = normalizeCode(po?.po_code);
  if (!normInvoicePo) {
    discrepancies.push({ type: "missing_po_number", detail: "Couldn't find a PO number referenced on the invoice." });
  } else if (normActualPo && !normActualPo.includes(normInvoicePo) && !normInvoicePo.includes(normActualPo)) {
    discrepancies.push({
      type: "po_number_mismatch",
      detail: `Invoice references PO "${extracted.po_number_on_invoice}", which doesn't match this PO (${po.po_code}).`,
    });
  }

  // Check 4: invoice qty/value shouldn't exceed this PO's own ordered
  // qty/value, regardless of GRN status.
  if (po?.qty_ordered != null && invoiceQty > 0) {
    const poQty = Number(po.qty_ordered);
    const tolerance = Math.max(poQty * 0.01, 1);
    if (invoiceQty - poQty > tolerance) {
      discrepancies.push({
        type: "qty_exceeds_po",
        detail: `Invoice qty ${invoiceQty} exceeds this PO's ordered qty ${poQty}.`,
      });
    }
  }
  if (po?.total_amount != null && invoiceTotal >= 0) {
    const poTotal = Number(po.total_amount);
    const tolerance = Math.max(poTotal * 0.01, 5);
    if (invoiceTotal - poTotal > tolerance) {
      discrepancies.push({
        type: "value_exceeds_po",
        detail: `Invoice total ₹${invoiceTotal} exceeds this PO's value ₹${poTotal}.`,
      });
    }
  }

  const hardDiscrepancies = discrepancies.filter((d) => !softTypes.has(d.type));
  const referenceLabel = grn ? `GRN ${grn.grn_code}` : "PO only (no matching GRN found in Uniware yet)";

  let status: string;
  if (hardDiscrepancies.length > 0) status = "mismatch";
  else if (grn) status = "matched";
  else status = "needs_review";

  const summary = status === "matched"
    ? `Matches ${referenceLabel} -- no discrepancies found.`
    : status === "mismatch"
    ? `${hardDiscrepancies.length} discrepanc${hardDiscrepancies.length === 1 ? "y" : "ies"} found.`
    : "No GRN raised against this PO yet in Uniware -- checked against the PO only.";

  return { status, summary, discrepancies, referenceLabel };
}

async function recordResult(adminClient: any, uploadId: number, status: string, summary: string, details: any) {
  await adminClient.from("po_invoice_uploads").update({
    match_status: status,
    match_summary: summary,
    match_details: details,
    checked_at: new Date().toISOString(),
  }).eq("id", uploadId);
  return json({ ok: true, match_status: status, match_summary: summary, match_details: details });
}

function arrayBufferToBase64(buf: ArrayBuffer) {
  let binary = "";
  const bytes = new Uint8Array(buf);
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
