# Lexcru PO Tracker

Auto-refreshing dashboard of Lexcru Water Tech Pvt Ltd (Uniware vendor code
`Vendor-156`) purchase orders across the 5 Native facilities (`PB-UC-GGN`,
`PB-UC-KOL`, `PB-UC-BLR`, `PB-UC-BOMBAY`, `PB-UC-HYD`), created 2026-08-01
onward.

Runs entirely on GitHub's infrastructure — no local machine, no Claude
involvement after setup. A GitHub Actions workflow fires every 5 minutes,
pulls fresh data straight from Uniware's REST API, and pushes an updated
`docs/index.html`. GitHub Pages serves that file as a live link.

## One-time setup

1. **Create the repo.** Push this folder to a new GitHub repository (public
   or private — public repos get unlimited free Actions minutes on standard
   runners, which matters at a 5-minute cadence; private repos get 2,000
   free minutes/month, which a `*/5 * * * *` schedule can burn through
   quickly since each run takes ~30-60s but there are ~8,640 runs/month).

2. **Add repo secrets** (Settings → Secrets and variables → Actions → New
   repository secret):
   - `UNIWARE_USERNAME`
   - `UNIWARE_PASSWORD`

   Use the same read-only Uniware account already used for other reporting
   scripts on Kalrav's machine — never the PO-create account.

3. **Enable GitHub Pages** (Settings → Pages):
   - Source: **Deploy from a branch**
   - Branch: `main`, folder: `/docs`

   Your hosted link will be `https://<your-username>.github.io/<repo-name>/`.

4. **Custom domain (optional):** add a `CNAME` file inside `docs/` containing
   your domain, and point a DNS `CNAME` record at
   `<your-username>.github.io`. GitHub's own docs cover the exact DNS
   records needed for apex vs. subdomain.

5. **Kick off the first run** manually: Actions tab → "Refresh Lexcru PO
   Tracker" → Run workflow. After that it runs unattended every 5 minutes.

## How it stays cheap and fast at a 5-minute cadence

Re-listing and re-fetching every PO since Aug 1 on every run would grow
without bound and hammer Uniware. Instead each run:

- Re-fetches full detail **only** for POs whose last known status isn't
  terminal (`COMPLETE` / `REJECTED` / `CANCELLED` / `CLOSED`) — those can
  still change (partial receipts, approval, rejection).
- Searches Uniware for new PO codes only in a short recent window (last 4
  days) per facility, to catch newly created POs.
- Carries forward terminal POs from the previous run's `docs/data.json`
  untouched.

`docs/data.json` is the full current dataset (also useful if you want to
build something else on top of it later); `docs/index.html` is the rendered
dashboard.

## Files

- `scripts/fetch_and_render.py` — the whole pipeline: auth, search, fetch,
  merge, render. Pure Python, no LLM calls anywhere.
- `.github/workflows/refresh.yml` — the 5-minute schedule.
- `docs/` — generated output, served by GitHub Pages. Committed by the bot,
  not hand-edited.

## Known constraints

- GitHub's scheduled workflows are not millisecond-precise — under load
  GitHub can delay a run by a few minutes. Treat "every 5 minutes" as "at
  least every 5-15 minutes in practice," not a hard guarantee.
- If Uniware ever rotates the read-only account's password, update the
  `UNIWARE_PASSWORD` secret — the workflow will otherwise start failing
  (visible in the Actions tab, which can be wired to email/Slack
  notifications via GitHub's own settings if wanted).
- `getPurchaseOrderDetails` masks `vendorName` on this account, so matching
  is done purely on `vendorCode == "Vendor-156"`.
