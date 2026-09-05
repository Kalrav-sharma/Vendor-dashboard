# Frontend (Vue 3 + Vite)

The entire vendor portal frontend now lives here, migrated off plain
HTML/vanilla JS onto Vue 3 (the migration ran in two stages: `login.html`
+ `reset-password.html` first as a pilot, then `vendor.html` + `admin.html`
+ `index.html`). `../docs/assets/` (the legacy `app-common.js`,
`supabase-client.js`, `styles.css`) has been deleted — nothing references
it anymore.

## Why this doesn't change how the site is hosted

GitHub Pages serves `../docs/` directly (Settings → Pages → Deploy from a
branch → `main` → `/docs`) — no CI build step, no changed Pages settings.
Instead, this project's build output gets **copied into `../docs/`
locally** (or by Claude Code) whenever a page changes, then committed and
pushed like any other file. See `build-and-deploy.sh`.

This deliberately avoids adding a GitHub Actions build/deploy workflow:
the existing Uniware sync workflow already commits to `../docs/.last_sync`
on its own schedule, and a second workflow also pushing to `docs/` would
risk racing it. Building locally and committing the result sidesteps that
entirely.

## Day to day

```
npm install             # first time only
npm run dev              # local dev server with hot reload, for iterating
npm run build             # production build -> dist/
./build-and-deploy.sh    # build + copy dist/ into ../docs/ (does the above for you)
```

After running `build-and-deploy.sh`, `git status` in the repo root will
show the changed files under `docs/` — commit and push those as usual.

## Structure

- `index.html`, `login.html`, `reset-password.html`, `vendor.html`,
  `admin.html` — Vite entry points (see `vite.config.js`'s
  `build.rollupOptions.input`)
- `src/VendorApp.vue`, `src/AdminApp.vue`, `src/LoginPage.vue`,
  `src/ResetPasswordPage.vue`, `src/index-main.js` — the actual pages
- `src/composables/` — the reactive logic, ported from the legacy pages'
  hand-rolled state management:
  - `usePurchaseOrders.js` — fetches purchase_orders/grns/po_items/
    grn_items, builds the lookup maps, polls every 60s
  - `usePoFilters.js` — PO Tracking's column filters + top search + sort.
    Being real reactive state (not manual innerHTML rebuilding) is what
    fixed the "typing loses focus" problem the legacy pages had to work
    around with a manual shell/tbody render split
  - `useSkuAggregates.js` — the SKU Level Data aggregation
  - `useVendors.js` — vendor login accounts (admin only)
  - `usePdfDownload.js` — calls the `get-po-pdf` Edge Function
  - `useModal.js` — the PO detail / SKU detail popups, as a small shared
    store rather than DOM injection
- `src/components/` — presentational pieces: `SidebarNav`, `StatusChip`,
  `PoTrackingTable`, `SkuLevelTable`, `PoDetailModal`, `SkuDetailModal`,
  `ManageVendors`, `AppModal`
- `src/supabaseClient.js` — shared Supabase client + `requireSession()`
  helper (the anon key here is safe to commit — see the comment in the
  file; real access control is Postgres Row Level Security, not this key)
- `src/format.js` — shared formatting/status helpers (`fmtNum`, `fmtMoney`,
  `fmtDate`, status labels/colors, the PO sort order)
- `src/shared.css` — design tokens + all page styles (single source of
  truth now that every page is migrated)

## Important: the `base` path in `vite.config.js`

Set to `/Vendor-dashboard/` to match this repo's GitHub Pages URL
(`https://kalrav-sharma.github.io/Vendor-dashboard/`). If the repo is ever
renamed, or Pages is moved to a custom domain, this needs to change to
match — otherwise the built pages' JS/CSS will 404 in production (they'll
still work fine locally under `npm run dev`/`preview`, which is what makes
this easy to miss).

## What wasn't (fully) verified before deploying

`vendor.html`/`admin.html` require a real logged-in session to show any
data, which only Kalrav has credentials for. Verified before deploying:
the production build succeeds, both pages load with zero console errors,
and the auth-guard redirect (no session → `login.html`) works correctly.
NOT verified by automation: the actual authenticated views (filters,
modals, SKU aggregation, PDF download, vendor management) — those need a
manual click-through pass with real credentials after each deploy that
touches these two pages.
