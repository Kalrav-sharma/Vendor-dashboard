# Implementation steps — Kalrav

Everything in this file needs you specifically: Supabase dashboard access,
your laptop (the only machine on the VPN that can reach Jarvis), and repo
settings. Praneeth built and merged the code; none of it does anything
until the steps below are done.

Pull `main` first — the code is already there.

---

## What was built, and the one thing not to undo

The Payment Dashboard's last column used to be a hardcoded grey chip
reading **"Pending integration"**, with a comment saying payment status
would arrive "once Oracle integration is built". It's now driven by real
data from Jarvis query
[594877](https://jarvis.urbanclap.com/queries/594877), which already
refreshes itself every 5 minutes.

**The column is labelled "Invoice status", not "Payment status" — and that
distinction is deliberate. Please don't collapse it back.**

Query 594877 has 27 columns and none of them says whether a vendor was
paid. The closest, `ORACLE_STATUS`, records whether the invoice *record*
was pushed into Oracle, which happens within hours of receipting and long
before money moves. Across all 93,406 rows, ~95% read `PUSHED`. Mapping
that to "Paid" would have told almost every vendor they'd been paid when
they hadn't — vendors would stop chasing real money, and Finance would
field the fallout. So the portal reports what the data actually says:

| Jarvis `ORACLE_STATUS` | Portal shows | Colour |
|---|---|---|
| `PUSHED` | In Oracle | green |
| `NOT_ATTEMPTED` | Not submitted | amber |
| `FAILED` | Submission failed | red |
| *(no match yet)* | Not synced | grey |

If a real payments source turns up later — Oracle AP, with a paid date and
UTR — that belongs in its own columns next to these, not folded into them.

### Why it's worth having anyway

In the Native facilities right now: **3,326 pushed · 52 not submitted ·
16 failed.** Those 16 are invoices that cannot be paid until someone fixes
them, with concrete reasons already in the data:

> *"You must provide a valid value for the Supplier Site…"*
> *"This invoice number already exists…"*

None of that was visible in the portal before. There's a new KPI tile,
**"Oracle submission failed"**, so it surfaces without hunting.

### Files

| File | What it is |
|---|---|
| `scripts/sync_jarvis_invoice_status.py` | Reads Jarvis, matches invoices, writes Supabase |
| `.github/workflows/sync-jarvis-invoice-status.yml` | Runs it every 15 min on your laptop |
| `.github/runner-setup.md` | Runner install reference (more detail than step 2 below) |
| `supabase/schema.sql` | New `oracle_*` columns on `po_invoice_uploads` |
| `frontend/src/format.js` | `ORACLE_STATUS_META` — the labels above |
| `frontend/src/components/InvoiceStatusChip.vue` | The chip |

Your OCR access-restriction work (`7cd91a7`) collided with this and was
merged by hand. Every file you touched — `reconciliation.js`,
`ReconciliationChip.vue`, `InvoiceUploads.vue`,
`check-invoice-match/index.ts` — is byte-identical to what you pushed.
Your removal of `isInternalStaff` from `reconciliationLabel` was kept.

---

## 1. Apply the schema

Supabase → **SQL Editor** → paste all of `supabase/schema.sql` → Run. It's
idempotent, so running the whole file is fine.

It adds `oracle_status`, `oracle_failure_remarks`, `oracle_synced_at` to
`po_invoice_uploads`, and drops four `payment_*` columns from an earlier
pass that turned out to describe a payment concept this data doesn't
contain. Nothing ever wrote to them, so there's no data to lose.

If the app then complains a column doesn't exist, that's PostgREST's stale
schema cache. Run this and retry:

```sql
NOTIFY pgrst, 'reload schema';
```

**Until this step is done every row shows "Not synced"** — the column
exists in the UI but has nothing to read.

## 2. Install the self-hosted runner on your laptop

This is the one workflow that can't run on GitHub. Jarvis is VPN /
IP-allowlist gated and GitHub's runners are on the public cloud, so this
job runs from your machine. The other 13 workflows are unchanged and stay
on GitHub — deliberately, so the portal doesn't stop syncing when your
laptop is shut.

GitHub → **Settings → Actions → Runners → New self-hosted runner →
Windows**, then follow the commands it gives you. Two things matter:

- **Add the label `vpn`** when `config.cmd` asks. The workflow targets
  `[self-hosted, vpn]`; without that label it will never pick your machine
  and the job just sits queued.
- **Say yes to running as a service**, so it restarts with the laptop
  instead of needing a terminal open.

The runner should show as **Idle** (green) on that page when it's done.
`.github/runner-setup.md` has the fuller version.

## 3. Add the Jarvis secret

GitHub → **Settings → Secrets and variables → Actions** → new secret
`JARVIS_API_KEY`. `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are
already there from the other workflows.

## 4. Rotate the Jarvis keys

Two Jarvis API keys were pasted into a chat session while this was being
built — a query key and the main account key. The account key reads all
93k rows across every UC business unit, not just Native. Please reissue
both and put the new account key into the secret in step 3.

## 5. Confirm the repo is private

A self-hosted runner executes whatever a workflow tells it to, on your
laptop. That's fine for a private repo where you know everyone with write
access. On a public repo it is not safe — anyone's pull request could run
code on your machine. The README already recommends this repo be private;
step 2 makes it a requirement.

## 6. Dry-run the sync

On the VPN, from the repo root:

```bash
JARVIS_API_KEY=... SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
  python scripts/sync_jarvis_invoice_status.py --dry-run
```

It writes nothing and prints a match breakdown. **Look at "matched on PO
only".**

Matching is on (PO code + invoice number), but our invoice number is
OCR-extracted from the uploaded PDF, so it can be missing or slightly off.
When it doesn't match, the script falls back to PO-level — but only when
every Jarvis row for that PO agrees on a status, so the answer is the same
whichever invoice it was. POs whose invoices disagree are skipped rather
than guessed at.

A small fallback count is expected. A large one means the OCR numbers
aren't lining up and the matching needs another look before a real run.

## 7. Run it for real

Drop `--dry-run`. Or trigger **Actions → Sync Jarvis invoice status to
Supabase → Run workflow** once the runner is up.

Two outcomes are both correct:

- It syncs and reports how many invoices it updated.
- It says *"Jarvis unreachable (VPN likely down) — skipping this run"* and
  ends **green**. That's the VPN preflight working, not a failure.

---

## Verify, then make it live

Open the Payment Dashboard as an internal login and check:

- The last column reads **Invoice status**, not "Payment status"
- Chips show In Oracle / Not submitted / Submission failed — not a wall of
  "Not synced" (that means step 1 didn't take)
- The **"Oracle submission failed"** KPI tile shows a non-zero count, and
  filtering to it lists real invoices
- Hovering a chip explains what the status means

**If that all looks right, please make it live** — confirm the runner is
installed as a service so it survives a reboot, leave the workflow on its
schedule, and let the team know the column is real now rather than a
placeholder. Worth telling Finance and Ops explicitly that it means
*submitted to Oracle*, not *paid*, so nobody reads it as a payment
confirmation.

---

## Known limitations

- **The sync only runs while your laptop is on and on the VPN.** Asleep or
  off-VPN means statuses go stale; nothing is lost, and the next run
  reconciles everything at once because the script reads full current
  state rather than replaying changes. Queued runs supersede each other,
  so coming back online replays one sync, not a day's backlog.
- **An open Payment Dashboard won't pick up new statuses until reload.**
  `useInvoiceUploads` fetches once on mount and doesn't poll, unlike
  `usePurchaseOrders` which polls every 60s. This predates the change and
  affects the reconciliation column too. It's roughly a five-line fix if
  you want it.
- **There's still no real payment status anywhere in the portal.** If
  Finance has an Oracle AP query with paid date and UTR, that's the
  missing piece, and the join key is already proven.
- **Schedule is every 15 minutes**, not 5 — the Jarvis query refreshes
  itself every 300s regardless, and submission status moves on the order
  of hours. One line at the top of the workflow if you want it faster.

## One unrelated heads-up

You'll see a `.claude/` directory and some automated commits. Praneeth's
Claude Code session is set to pull, commit and push automatically on
`main`, which means changes reach the live site without a review step.
Worth knowing when you see commits you didn't make.
