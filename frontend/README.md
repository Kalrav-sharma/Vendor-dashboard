# Frontend (Vue 3 + Vite) — staged migration from plain HTML/JS

This is a **staged migration** of the vendor portal's frontend off plain
HTML/vanilla JS onto Vue 3, since the app is growing complex enough
(modals, filters, aggregations) that a component framework earns its
keep. So far, migrated: `login.html`, `reset-password.html`. Still plain
HTML for now: `vendor.html`, `admin.html`, `index.html` — those live only
in `../docs/` and this project doesn't touch them.

## Why this doesn't change how the site is hosted

GitHub Pages serves `../docs/` directly (Settings → Pages → Deploy from a
branch → `main` → `/docs`) — no CI build step, no changed Pages settings.
Instead, this project's build output gets **copied into `../docs/`
locally** (or by Claude Code) whenever a migrated page changes, then
committed and pushed like any other file. See `build-and-deploy.sh`.

This deliberately avoids adding a GitHub Actions build/deploy workflow:
the existing Uniware sync workflow already commits to `../docs/.last_sync`
on its own schedule, and a second workflow also pushing to `docs/` would
risk racing it. Building locally and committing the result sidesteps that
entirely.

## Day to day

```
npm install        # first time only
npm run dev         # local dev server with hot reload, for iterating
npm run build       # production build -> dist/
./build-and-deploy.sh   # build + copy dist/ into ../docs/ (does the above for you)
```

After running `build-and-deploy.sh`, `git status` in the repo root will
show the changed files under `docs/` — commit and push those as usual.

## Structure

- `login.html`, `reset-password.html` — Vite entry points (see
  `vite.config.js`'s `build.rollupOptions.input`)
- `src/LoginPage.vue`, `src/ResetPasswordPage.vue` — the actual pages
- `src/supabaseClient.js` — shared Supabase client + `requireSession()`
  helper (the anon key here is safe to commit — see the comment in the
  file; real access control is Postgres Row Level Security, not this key)
- `src/shared.css` — design tokens + auth-page styles, currently a
  duplicate of the relevant parts of `../docs/assets/styles.css`. Once
  `vendor.html`/`admin.html` migrate too, this becomes the single source
  of truth and the legacy copy goes away.

## Important: the `base` path in `vite.config.js`

Set to `/Vendor-dashboard/` to match this repo's GitHub Pages URL
(`https://kalrav-sharma.github.io/Vendor-dashboard/`). If the repo is ever
renamed, or Pages is moved to a custom domain, this needs to change to
match — otherwise the built pages' JS/CSS will 404 in production (they'll
still work fine locally under `npm run dev`/`preview`, which is what makes
this easy to miss).
