# CLAUDE.md

## Project overview
This repository is the Vendor Portal dashboard for a vendor-facing purchase-order and GRN tracking app.

- Frontend source: `frontend/`
- Built static output served from GitHub Pages: `docs/`
- Supabase auth and data layer: `supabase/`
- Background sync jobs: `scripts/`
- GitHub automation: `.github/workflows/`

The app is built around a Vue 3 + Vite frontend that talks to Supabase directly. RLS in Postgres is the real authorization boundary; the UI should not be trusted as the only access control mechanism.

## Local setup

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Production build
```bash
cd frontend
npm run build
```

Then publish the generated app to the static docs site via:
```bash
cd frontend
./build-and-deploy.sh
```

### Python scripts
```bash
pip install -r requirements.txt
```

## Important repo structure

- `frontend/src/` — Vue app source and page entry points
- `frontend/src/components/` — reusable UI components
- `docs/` — static HTML and built Vite assets used by GitHub Pages
- `supabase/schema.sql` — database schema and RLS policies
- `supabase/functions/` — Supabase Edge Functions
- `scripts/` — sync scripts for Uniware / inventory / tracking data
- `.github/workflows/` — scheduled refresh automation

## App conventions
- Prefer editing the Vue source in `frontend/src/` rather than the generated files in `docs/`.
- After frontend changes, rebuild and refresh the static output if the app is expected to match the published GitHub Pages version.
- Keep credentials and secrets out of source control. Only use environment variables or Supabase secrets.
- If data access rules are involved, validate them against Supabase RLS and the project design rather than trusting frontend-only checks.

## Working rules for Claude
- Keep work scoped to the repo and avoid unrelated changes.
- Favor small, targeted edits with clear intent.
- When changing shared frontend behavior, check whether the corresponding page entry points or components need updates in multiple places.
- Respect the distinction between app source and generated static deployment content.
- Do not add hardcoded auth tokens, service-role keys, or vendor credentials to files.

## Common entry points
- Login page: `frontend/src/LoginPage.vue`
- Vendor dashboard: `frontend/src/VendorApp.vue`
- Admin console: `frontend/src/AdminApp.vue`
- Shared Supabase config: `frontend/src/supabaseClient.js`
- Static site entry files: `docs/login.html`, `docs/vendor.html`, `docs/admin.html`

## Deployment note
This repo is designed to serve a static GitHub Pages site out of `docs/`, while the active development happens in `frontend/`. Keep those two aligned when preparing a release.
