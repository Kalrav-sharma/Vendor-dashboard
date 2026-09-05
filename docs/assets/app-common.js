// Shared formatting/rendering helpers for vendor.html and admin.html.
// Loaded after supabase-client.js, before each page's own inline script.

const TERMINAL_STATUSES = new Set(["COMPLETE", "REJECTED", "CANCELLED", "CLOSED"]);

// Rejected and not-yet-approved POs are hidden from both the vendor and
// admin views entirely (Kalrav's explicit requirement) -- CREATED is
// Uniware's "drafted, not yet approved" status.
const HIDDEN_STATUSES = new Set(["REJECTED", "CREATED"]);

const STATUS_META = {
  COMPLETE: ["Complete", "good"],
  APPROVED: ["Approved · open", "open"],
  CREATED: ["Created · open", "open"],
  REJECTED: ["Rejected", "critical"],
  CANCELLED: ["Cancelled", "muted"],
};

function fmtNum(n) {
  return (n === null || n === undefined) ? "–" : Number(n).toLocaleString("en-IN");
}

function fmtMoney(n) {
  return (n === null || n === undefined) ? "–" : "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtDate(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }) + ", " +
         d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

function statusChip(status) {
  const [label, cls] = STATUS_META[status] || [status || "Unknown", "muted"];
  return `<span class="chip chip-${cls}">${label}</span>`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function visiblePos(pos) {
  return pos.filter(p => !HIDDEN_STATUSES.has(p.status));
}

// --- Real Uniware PO PDF download ---
// Calls the get-po-pdf Edge Function, which fetches the actual official
// Uniware document (not something we generate) and streams it back --
// authorization is enforced by the same RLS every other query relies on,
// so this can only ever return a PO the caller is allowed to see.
async function downloadPoPdf(client, poCode, buttonEl) {
  const originalLabel = buttonEl ? buttonEl.textContent : null;
  if (buttonEl) {
    buttonEl.disabled = true;
    buttonEl.textContent = "Fetching…";
  }
  try {
    const { data, error } = await client.functions.invoke(
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
    if (buttonEl) {
      buttonEl.disabled = false;
      buttonEl.textContent = originalLabel;
    }
  }
}

// Wires up a left-sidebar nav: sections = [{ navId, viewId }, ...].
// Clicking a nav button shows its view, hides the others, marks it active.
// Returns a `showSection(navId, onShow)` function so pages can switch
// sections programmatically (e.g. clicking a PO code jumps to SKU data).
function setupSidebarNav(sections, onShow) {
  function showSection(navId) {
    for (const { navId: nid, viewId } of sections) {
      const isActive = nid === navId;
      document.getElementById(viewId).hidden = !isActive;
      document.getElementById(nid).classList.toggle("active", isActive);
    }
    if (onShow) onShow(navId);
  }
  for (const { navId } of sections) {
    document.getElementById(navId).addEventListener("click", () => showSection(navId));
  }
  return showSection;
}
