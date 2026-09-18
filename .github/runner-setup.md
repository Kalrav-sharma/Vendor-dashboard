# Self-hosted runner (the Jarvis sync)

One workflow in this repo — `sync-jarvis-invoice-status.yml` — cannot run
on GitHub's servers. It reads Jarvis (`jarvis.urbanclap.com`), which is
only reachable from inside Urban Company's network, and GitHub's runners
are on the public cloud. So that one job runs on a laptop that is already
on the VPN. Everything else stays on GitHub and is unaffected by this.

Setting this up is a one-time job on the machine that will host it.

## What you're signing up for

The sync runs only while that laptop is **switched on and connected to the
VPN**. Asleep, shut, or off VPN means no sync — invoice statuses in the
portal go stale until it's back. Nothing breaks and nothing is lost; the
next run reconciles everything at once, because the script always reads
current state rather than replaying changes.

The rest of the portal — purchase orders, GRNs, courier tracking, S&OP —
keeps syncing from GitHub regardless. Only the Payment Dashboard's
"Invoice status" column depends on this machine.

## Install the runner

1. In GitHub: **Settings → Actions → Runners → New self-hosted runner**,
   and pick **Windows**.
2. Follow the download/configure commands GitHub shows you. They're
   copy-pasteable and include a token that's only valid for an hour.
3. When `config.cmd` asks for **labels**, add `vpn`. This is the part that
   matters — the workflow targets `[self-hosted, vpn]`, so without that
   label the job will never pick this machine.
4. When it asks to run as a service, say **yes**. That's what makes the
   sync start on its own after a reboot instead of waiting for someone to
   open a terminal.

Confirm it worked: the runner shows as **Idle** (green) on that Runners
page.

## Add the secrets

**Settings → Secrets and variables → Actions**, three of them:

| Secret | Value |
|---|---|
| `JARVIS_API_KEY` | A Jarvis account API key |
| `SUPABASE_URL` | Already set for the other workflows |
| `SUPABASE_SERVICE_ROLE_KEY` | Already set for the other workflows |

Only the Jarvis key is new. GitHub injects these into the job, so nothing
needs to sit in a file on the laptop.

## Requirements on the machine

- **Python 3.11+** on PATH. The workflow calls `actions/setup-python`,
  which handles this itself on most setups, but a system Python avoids any
  surprises.
- **Git**, which you'll already have.

## Check it

**Actions → Sync Jarvis invoice status to Supabase → Run workflow.** Two
outcomes are both fine:

- It runs the sync and reports how many invoices it updated.
- It says *"Jarvis unreachable (VPN likely down) — skipping this run"* and
  ends green. That's the VPN check doing its job, not a failure. Connect
  and run it again.

## Things worth knowing

- **A failed run isn't always a problem.** Off-VPN runs end cleanly with a
  notice. A genuinely red run means something else — start with the log.
- **Backlogs don't pile up.** If the laptop is closed for a day, the
  queued runs supersede each other and coming back online replays one
  sync, not a hundred.
- **Keep the repo private.** A self-hosted runner executes whatever a
  workflow tells it to, on this machine. That's fine for a private repo
  where you know everyone with write access; it is not safe on a public
  one, where anyone's pull request could run code here.
- **Changing the schedule** is one line at the top of
  `sync-jarvis-invoice-status.yml`. It's every 15 minutes now.
