#!/usr/bin/env bash
# SessionStart hook -- pull teammate/bot commits before any work starts.
#
# This repo has 13 scheduled GitHub Actions pushing to main on their own
# (the Uniware sync commits docs/.last_sync every ~5 min, plus courier
# tracking, rate card, and seven S&OP syncs), so a checkout goes stale
# within minutes of sitting idle. Fetching at session start means edits
# are always made on top of current main.
#
# Deliberately --ff-only: if main has genuinely diverged (local commits
# not yet pushed), this reports it rather than creating a merge commit
# nobody asked for. sync-push.sh does the real merge, after committing,
# where a conflict can be resolved safely.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO" || exit 0

emit() { printf '{"systemMessage": "%s", "suppressOutput": true}\n' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }

git fetch origin --quiet 2>/dev/null || { emit "git fetch failed (offline?) -- working from the local checkout."; exit 0; }

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
[ "$BRANCH" = "main" ] || { emit "On branch '$BRANCH', not main -- skipped auto-pull."; exit 0; }

BEHIND="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)"
AHEAD="$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)"

[ "$BEHIND" = "0" ] && exit 0

if [ "$AHEAD" != "0" ]; then
  emit "main has diverged: $AHEAD local commit(s), $BEHIND remote. Not auto-merging -- resolve before pushing."
  exit 0
fi

if git merge --ff-only origin/main --quiet 2>/dev/null; then
  emit "Pulled $BEHIND new commit(s) from origin/main."
else
  emit "Could not fast-forward $BEHIND commit(s) -- local changes may be in the way."
fi
exit 0
