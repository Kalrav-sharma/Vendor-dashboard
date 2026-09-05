# Vendor PO Tracker (with auth)

A live portal where vendors log in and see only their own purchase orders
and GRN/invoice status, pulled automatically from Uniware. Currently scoped
to one vendor (Lexcru Water Tech, Uniware vendor code `Vendor-156`) across
5 Native facilities (`PB-UC-GGN`, `PB-UC-KOL`, `PB-UC-BLR`, `PB-UC-BOMBAY`,
`PB-UC-HYD`), purchase orders created 2026-08-01 onward.

**Architecture:**
- **GitHub Actions** (`.github/workflows/refresh.yml`) — runs every 5
  minutes, pure Python, zero LLM/human involvement. Pulls fresh PO + GRN
  data from Uniware and writes it into Supabase.
- **Supabase Postgres** — the database of record. Row Level Security (RLS)
  enforces that a vendor login can only ever query rows matching their own
  `vendor_code`; an admin login can query everything.
- **Supabase Auth** — real login/password, managed by you (admin) via the
  admin console's "Create vendor login" form.
- **Supabase Edge Function** (`admin-create-vendor`) — the only place vendor
  accounts get created. Runs server-side so the highly-privileged
  `service_role` key never touches the browser.
- **GitHub Pages** (`docs/`) — serves the static frontend: `login.html`,
  `vendor.html` (vendor dashboard), `admin.html` (admin console). All three
  talk to Supabase directly via `supabase-js`; RLS does the real access
  control, not the frontend code.
- **`frontend/`** — a Vue 3 + Vite project, mid-migration off plain
  HTML/vanilla JS (`login.html` and `reset-password.html` are migrated so
  far; `vendor.html`/`admin.html` are still plain HTML in `docs/`). Its
  build output gets copied into `docs/` locally (`frontend/build-and-
  deploy.sh`), not via a CI workflow — see `frontend/README.md` for why.

## One-time setup

### 1. Create the Supabase project
1. supabase.com → sign up → **New Project**. Set a strong DB password (you
   won't need to give it to me/anyone — it's separate from vendor logins).
2. Project Settings → API. Note down:
   - **Project URL** (`https://xxxx.supabase.co`)
   - **`anon` `public` key**
   - **`service_role` key** — treat as a master password to your whole
     database. Never commit it, never put it in any file under `docs/`,
     never paste it anywhere but the two places below.

### 2. Run the database schema
Project → **SQL Editor** → New query → paste the entire contents of
`supabase/schema.sql` → Run. This creates the `profiles`, `purchase_orders`
and `grns` tables with RLS policies already attached.

### 3. Bootstrap your own admin login
1. Project → **Authentication → Users → Add user** → create your own
   email + password. Copy the generated User UID.
2. Back in the SQL Editor, run (see the bottom of `supabase/schema.sql` for
   this exact snippet):
   ```sql
   insert into public.profiles (id, role, vendor_name, email)
   values ('<your-user-uid>', 'admin', 'Kalrav (admin)', '<your-email>');
   ```
   This is the only account that ever gets `role='admin'` this way — every
   vendor account after this gets created through the admin console itself.

### 4. Deploy the Edge Functions
There are two. Easiest path, no CLI needed: Project → **Edge Functions** →
**Create a new function** for each, name it exactly as below, paste in the
matching file's contents, Deploy.

- `admin-create-vendor` ← `supabase/functions/admin-create-vendor/index.ts`
  — the only place vendor accounts get created.
- `get-po-pdf` ← `supabase/functions/get-po-pdf/index.ts` — fetches the
  real, official Uniware PO PDF on demand (confirmed working: Uniware's own
  `/po/show?legacy=1&code=...` endpoint accepts the same OAuth token our
  read-only sync account already uses, no browser/cookie session needed).
  Authorization for this one piggybacks on the same RLS as everything
  else — it queries `purchase_orders` as the calling user, so a vendor
  requesting a PO they don't own just gets "not found."

(If you'd rather use the CLI: `supabase login`, `supabase link --project-ref
<your-project-ref>`, then `supabase functions deploy admin-create-vendor`
and `supabase functions deploy get-po-pdf` from this repo root.)

Then set each function's required secrets — Project → Edge Functions →
(function name) → Secrets (or `supabase secrets set NAME=value`):
- `admin-create-vendor` needs `SUPABASE_SERVICE_ROLE_KEY` = the service_role
  key from step 1.
- `get-po-pdf` needs `UNIWARE_USERNAME` and `UNIWARE_PASSWORD` = the same
  read-only Uniware account already used by the GitHub Actions sync script.

(`SUPABASE_URL` and `SUPABASE_ANON_KEY` are auto-injected by Supabase into
every Edge Function — you don't set those yourself.)

### 5. Fill in the frontend's public config
Edit `docs/assets/supabase-client.js` in this repo, replacing:
- `__SUPABASE_URL__` with your Project URL
- `__SUPABASE_ANON_KEY__` with your `anon` `public` key

Both are safe to commit — they grant no access on their own; every table's
RLS policy is what actually decides who can read what.

### 6. Push to GitHub
```
cd ~/lexcru-po-tracker
git remote add origin https://github.com/<you>/<repo>.git
git branch -M main
git push -u origin main
```

### 7. Add GitHub Actions secrets
Repo → **Settings → Secrets and variables → Actions → New repository
secret**, four of them:
- `UNIWARE_USERNAME`, `UNIWARE_PASSWORD` — same read-only Uniware account
  used elsewhere.
- `SUPABASE_URL` — same Project URL as above.
- `SUPABASE_SERVICE_ROLE_KEY` — same service_role key as step 4. This is
  the second (and last) place this key should ever be pasted.

### 8. Turn on GitHub Pages
Repo → **Settings → Pages** → Source: **Deploy from a branch** → Branch
`main`, folder **`/docs`** → Save.

Your portal's link: `https://<you>.github.io/<repo>/` — lands on
`login.html` if you're not signed in.

### 9. Fire the first sync manually
Repo → **Actions** → "Sync Uniware PO/GRN data to Supabase" → **Run
workflow**. Don't wait for the cron — trigger it once by hand so the
database is populated before you first open the portal.

### 10. Allow the "Forgot password" redirect
Supabase only honors a password-reset link's `redirectTo` URL if it's on an
allowlist — otherwise it silently falls back to whatever Site URL is
configured (often `localhost`), and the reset link goes nowhere useful.

Supabase Dashboard → **Authentication → URL Configuration**:
- **Site URL**: your Pages URL, e.g. `https://<you>.github.io/<repo>/`
- **Redirect URLs**: add `https://<you>.github.io/<repo>/reset-password.html`
  (or a wildcard like `https://<you>.github.io/<repo>/*`)

Without this step, a vendor clicking "Forgot password?" will get a reset
email, but the link in it won't land back on `reset-password.html`
correctly.

## Using it day to day

- **Sign in as admin** at your Pages URL → lands on `admin.html`. Use
  "Create vendor login" to add a new vendor: their email, a temporary
  password you tell them directly (shown once, not stored anywhere
  retrievable), their Uniware vendor code, and a display name. The vendor
  filter dropdown below lets you view any single vendor's POs, or all of
  them together.
- **A vendor signs in** with the email/password you gave them → lands on
  `vendor.html`, sees only their own POs and GRNs — enforced by Postgres
  RLS, not by anything the frontend chooses to show.
- To onboard a new vendor beyond Lexcru: add their vendor code to
  `VENDOR_CODE`/`FACILITIES` handling in `scripts/sync_to_supabase.py` (this
  script is currently single-vendor; extending it to loop over a list of
  vendor codes is the next natural step once you're ready to onboard more
  than one), then create their login from the admin console.

## Known constraints

- GitHub's scheduled cron isn't millisecond-precise — treat "every 5
  minutes" as "every 5-15 minutes in practice."
- `getPurchaseOrderDetails` masks `vendorName` on this Uniware account, so
  matching is done purely on `vendorCode`.
- This repo + its GitHub Pages site should be **private** before any real
  vendor uses it for anything beyond a live test — a public repo makes the
  static frontend files (not the data — that's gated by Supabase RLS) world
  readable, and a public GitHub Pages URL is unauthenticated at the HTTP
  level even though the data behind login is properly access-controlled.
  Private repos need GitHub Enterprise for private Pages, or Pages can be
  swapped for something like Cloudflare Pages/Vercel with access control at
  the hosting layer instead.
- Passwords you set for vendors are temporary — there's currently no
  "vendor changes their own password" or "forgot password" flow wired into
  `login.html`; Supabase Auth supports both, just not built into this UI
  yet.
