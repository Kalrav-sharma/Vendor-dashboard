// AI-assisted invoice-vs-PO/GRN match check. Runs automatically right
// after a vendor (or admin) uploads an invoice PDF (see uploadInvoice()
// in useInvoiceUploads.js), and can be re-run manually via the
// "Re-check" button -- useful once a GRN shows up later in Uniware for
// an invoice that was originally checked against the PO alone.
//
// Design: Claude only does the OCR/extraction (reading the PDF's
// invoice number/date/line items/total), forced via tool_choice into
// one strict tool call so the output is always structured. All actual
// matching logic (which GRN this invoice corresponds to, numeric
// tolerances, what counts as a discrepancy) is plain deterministic
// TypeScript below -- not something the model is asked to judge. That
// keeps the comparison auditable and doesn't depend on an LLM's
// arithmetic being right.
//
// Match precedence: if the extracted invoice number matches a GRN
// Uniware already recorded against this PO, compare against that GRN's
// own line items/received amount (the actual receipt). Otherwise fall
// back to comparing against the PO's ordered line items, and mark the
// result "needs_review" rather than a hard pass/fail, since a PO can
// span several dispatch batches and no GRN means nothing's confirmed
// received yet for this specific invoice.
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
    // private file, and reading po_items/grns/grn_items regardless of
    // whose vendor_code they belong to (already authorized above).
    const adminClient = createClient(SUPABASE_URL, SERVICE_ROLE_KEY);

    const { data: fileBlob, error: downloadErr } = await adminClient.storage.from(BUCKET).download(upload.storage_path);
    if (downloadErr || !fileBlob) {
      return await recordResult(adminClient, upload.id, "error", "Couldn't read the uploaded file back from storage.", null);
    }

    const [{ data: poItems }, { data: grns }] = await Promise.all([
      adminClient.from("po_items").select("item_sku, item_name, quantity, unit_price, total").eq("po_code", upload.po_code),
      adminClient.from("grns").select("*").eq("po_code", upload.po_code),
    ]);

    let extracted;
    try {
      const pdfBase64 = arrayBufferToBase64(await fileBlob.arrayBuffer());
      extracted = await extractInvoiceData(pdfBase64, poItems || []);
    } catch (e) {
      return await recordResult(
        adminClient, upload.id, "error",
        `Couldn't read the invoice PDF: ${e instanceof Error ? e.message : String(e)}`, null
      );
    }

    const matchingGrn = (grns || []).find((g) => {
      const gNo = normalizeInvoiceNo(g.vendor_invoice_number);
      return gNo && gNo === normalizeInvoiceNo(extracted.invoice_number);
    });

    let grnItems: any[] = [];
    if (matchingGrn) {
      const { data } = await adminClient
        .from("grn_items").select("item_sku, quantity, unit_price").eq("grn_code", matchingGrn.grn_code);
      grnItems = data || [];
    }

    const { status, summary, discrepancies, referenceLabel } = compareInvoiceToReference(
      extracted, poItems || [], matchingGrn, grnItems
    );

    return await recordResult(adminClient, upload.id, status, summary, {
      extracted, reference: referenceLabel, discrepancies,
    });
  } catch (e) {
    return json({ error: `Unexpected error: ${e instanceof Error ? e.message : String(e)}` }, 500);
  }
});

async function extractInvoiceData(pdfBase64: string, poItemsHint: any[]) {
  const tool = {
    name: "report_invoice_extraction",
    description: "Report the structured data read off the invoice PDF.",
    input_schema: {
      type: "object",
      properties: {
        invoice_number: { type: "string", description: "Invoice/document number as printed. Empty string if not found." },
        invoice_date: { type: "string", description: "Invoice date as ISO YYYY-MM-DD if determinable, else empty string." },
        grand_total: { type: "number", description: "Invoice grand total amount. Use -1 if not determinable." },
        line_items: {
          type: "array",
          items: {
            type: "object",
            properties: {
              sku: { type: "string", description: "SKU/item code as printed, empty string if not shown." },
              description: { type: "string" },
              quantity: { type: "number", description: "Use -1 if not determinable." },
              unit_price: { type: "number", description: "Use -1 if not determinable." },
              amount: { type: "number", description: "Use -1 if not determinable." },
            },
            required: ["sku", "description", "quantity", "unit_price", "amount"],
          },
        },
        extraction_confidence: { type: "string", enum: ["high", "medium", "low"] },
      },
      required: ["invoice_number", "invoice_date", "grand_total", "line_items", "extraction_confidence"],
    },
  };

  const hintText = poItemsHint.length
    ? "For reference, this purchase order's own line items are:\n" +
      poItemsHint.map((i: any) => `- SKU ${i.item_sku}: ${i.item_name || ""} (ordered qty ${i.quantity})`).join("\n") +
      "\nUse this only to help identify which printed code/description on the invoice corresponds to which SKU -- " +
      "read the invoice's own printed quantities/prices, don't copy these reference values."
    : "";

  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: ANTHROPIC_MODEL,
      max_tokens: 4096,
      tools: [tool],
      tool_choice: { type: "tool", name: "report_invoice_extraction" },
      messages: [{
        role: "user",
        content: [
          { type: "document", source: { type: "base64", media_type: "application/pdf", data: pdfBase64 } },
          {
            type: "text",
            text: "Read this vendor invoice PDF and extract its data via the report_invoice_extraction tool. " +
              "Read every line item exactly as printed -- don't guess or round.\n\n" + hintText,
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

function normalizeInvoiceNo(s: string | null | undefined) {
  return (s || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
}
function normalizeSku(s: string | null | undefined) {
  return (s || "").trim().toUpperCase();
}

function compareInvoiceToReference(extracted: any, poItems: any[], grn: any, grnItems: any[]) {
  const discrepancies: { type: string; detail: string }[] = [];

  if (extracted.extraction_confidence === "low") {
    discrepancies.push({ type: "low_confidence", detail: "Low OCR confidence reading this PDF -- please review the file directly." });
  }
  if (!extracted.invoice_number) {
    discrepancies.push({ type: "missing_invoice_number", detail: "Couldn't read an invoice number off the PDF." });
  }

  const referenceItems = grn ? grnItems : poItems;
  const referenceLabel = grn ? `GRN ${grn.grn_code}` : "PO (no matching GRN found in Uniware yet)";
  const refBySku = new Map(
    referenceItems.filter((i: any) => i.item_sku).map((i: any) => [normalizeSku(i.item_sku), i])
  );

  for (const line of extracted.line_items || []) {
    const sku = normalizeSku(line.sku);
    const ref: any = sku ? refBySku.get(sku) : null;
    if (!ref) {
      discrepancies.push({
        type: "unmatched_line",
        detail: `Invoice line "${line.description || line.sku || "(no SKU/description)"}" doesn't match any SKU on the ${referenceLabel}.`,
      });
      continue;
    }
    if (line.quantity >= 0 && ref.quantity != null && Math.abs(line.quantity - Number(ref.quantity)) > 0.01) {
      discrepancies.push({
        type: "qty_mismatch",
        detail: `SKU ${sku}: invoice qty ${line.quantity} vs ${referenceLabel} qty ${ref.quantity}.`,
      });
    }
    if (line.unit_price >= 0 && ref.unit_price != null) {
      const refPrice = Number(ref.unit_price);
      const pctDiff = refPrice > 0 ? Math.abs(line.unit_price - refPrice) / refPrice : 0;
      if (pctDiff > 0.02) {
        discrepancies.push({
          type: "price_mismatch",
          detail: `SKU ${sku}: invoice unit price ₹${line.unit_price} vs ${referenceLabel} unit price ₹${refPrice}.`,
        });
      }
    }
  }

  if (grn && grn.total_received_amount != null && extracted.grand_total >= 0) {
    const refTotal = Number(grn.total_received_amount);
    const tolerance = Math.max(refTotal * 0.02, 5);
    if (Math.abs(extracted.grand_total - refTotal) > tolerance) {
      discrepancies.push({
        type: "total_mismatch",
        detail: `Invoice total ₹${extracted.grand_total} vs ${referenceLabel} received amount ₹${refTotal}.`,
      });
    }
  }

  // Low OCR confidence / a missing invoice number are flagged for a human
  // to look at, but don't by themselves count as a hard "mismatch".
  const hardDiscrepancies = discrepancies.filter((d) => d.type !== "low_confidence" && d.type !== "missing_invoice_number");

  let status: string;
  if (hardDiscrepancies.length > 0) status = "mismatch";
  else if (grn) status = "matched";
  else status = "needs_review";

  const summary = status === "matched"
    ? `Matches ${referenceLabel} -- no discrepancies found.`
    : status === "mismatch"
    ? `${hardDiscrepancies.length} discrepanc${hardDiscrepancies.length === 1 ? "y" : "ies"} found against the ${referenceLabel}.`
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
