#!/usr/bin/env bash
# Stop hook -- commit and push finished work to main automatically.
#
# Order matters here, and it encodes this repo's two hard rules:
#
#   1. docs/ is BUILD OUTPUT. If anything under frontend/src/ changed, the
#      build runs BEFORE staging, so a source change never ships without
#      its rebuilt docs/. Committing one without the other leaves the live
#      GitHub Pages site disagreeing with the source that produced it.
#
#   2. Commit BEFORE merging origin/main. Scheduled bots push to main every
#      ~5 min, so a push from a stale checkout is rejected. Merging a dirty
#      tree fails outright, so the commit has to come first -- and then a
#      docs/ conflict is resolved the only correct way: re-run the build and
#      take its output, never a hand-merge of generated files.
#
# Pushing to main here IS a production deploy (Pages serves docs/ off main).
# That was an explicit opt-in; see .claude/settings.json.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO" || exit 0

MSG_FILE="$REPO/.claude/.commit-msg"
ATTRIB="Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"

emit() { printf '{"systemMessage": "%s", "suppressOutput": true}\n' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
[ "$BRANCH" = "main" ] || exit 0          # never auto-push a feature branch
git rev-parse --verify MERGE_HEAD >/dev/null 2>&1 && {
  emit "A merge is already in progress -- resolve it before auto-push resumes."; exit 0; }

# Nothing to do? Stay silent. This is the common case on a read-only /
# exploratory turn, and a hook that narrates every no-op is just noise.
[ -z "$(git status --porcelain)" ] && exit 0

# --- 1. Rebuild docs/ if the Vue source moved -------------------------------
if ! git status --porcelain | grep -q '^.. frontend/src/'; then :; else
  if ! bash frontend/build-and-deploy.sh >/tmp/vp-build.log 2>&1; then
    emit "frontend/src changed but the build FAILED -- nothing committed or pushed. See /tmp/vp-build.log"
    exit 0
  fi
fi

# --- 2. Commit --------------------------------------------------------------
if [ -f "$MSG_FILE" ] && [ -s "$MSG_FILE" ]; then
  SUBJECT="$(head -n1 "$MSG_FILE")"
else
  # No message was staged for this turn -- derive one from the touched areas
  # rather than inventing a description of work this script cannot see.
  SUBJECT="Update $(git status --porcelain | awk '{print $2}' | xargs -n1 dirname 2>/dev/null | sort -u | head -3 | paste -sd', ' -)"
fi

git add -A
git commit -q -m "$SUBJECT" -m "$ATTRIB" 2>/dev/null || { emit "Nothing staged to commit."; exit 0; }
rm -f "$MSG_FILE"

# --- 3. Merge origin/main, resolving docs/ the only correct way -------------
git fetch origin --quiet 2>/dev/null
if ! git merge origin/main --no-edit --quiet >/dev/null 2>&1; then
  CONFLICTS="$(git diff --name-only --diff-filter=U)"
  if [ -n "$CONFLICTS" ] && [ -z "$(printf '%s\n' "$CONFLICTS" | grep -v '^docs/')" ]; then
    # Generated files only -- rebuild and take the build's output as truth.
    git checkout --ours docs/.last_sync 2>/dev/null || true
    if bash frontend/build-and-deploy.sh >/tmp/vp-build.log 2>&1; then
      git add -A && git commit -q --no-edit 2>/dev/null
    else
      git merge --abort 2>/dev/null
      emit "docs/ conflicted and the rebuild failed -- committed locally, NOT pushed."
      exit 0
    fi
  else
    # Roll back so the next session starts from a clean tree. The abort can
    # itself fail (it did, 2026-09-18, leaving the repo mid-merge with the
    # failure swallowed by 2>/dev/null) -- so verify it actually worked and
    # say so loudly if it didn't, rather than reporting a tidy "not pushed"
    # over a repo that is in fact wedged.
    git merge --abort 2>/dev/null
    FILES="$(printf '%s' "$CONFLICTS" | tr '\n' ' ')"
    if git rev-parse --verify MERGE_HEAD >/dev/null 2>&1; then
      emit "CONFLICT outside docs/ AND the automatic abort FAILED -- repo is mid-merge and needs hands-on resolution now: $FILES"
    else
      emit "Merge conflict outside docs/ -- committed locally, NOT pushed, merge rolled back. Resolve by hand: $FILES"
    fi
    exit 0
  fi
fi

# --- 4. Push ----------------------------------------------------------------
if git push origin main --quiet 2>/dev/null; then
  emit "Committed and pushed to main: $SUBJECT -- live site updating."
else
  emit "Committed locally but PUSH FAILED (auth or a fresh remote commit). Run: git fetch origin && git merge origin/main && git push"
fi
exit 0
