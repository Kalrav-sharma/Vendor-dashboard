#!/usr/bin/env bash
# Builds this Vite project and copies its output into ../docs/, which is
# what GitHub Pages actually serves (Settings -> Pages -> Deploy from a
# branch -> main -> /docs). This is NOT run by any CI workflow -- it's run
# locally (or by Claude Code) whenever a page here changes, then the
# updated docs/ files get committed and pushed like any other change.
#
# Deliberately does NOT touch anything else in docs/ (vendor.html,
# admin.html, docs/assets/, docs/.last_sync) -- those belong to pages not
# yet migrated off plain HTML, or to the separate Uniware sync workflow.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCS_DIR="$SCRIPT_DIR/../docs"

cd "$SCRIPT_DIR"
npm run build

# Remove only what a previous run of this script put there, so a renamed/
# removed page or a changed asset hash doesn't leave stale files behind.
rm -f "$DOCS_DIR/login.html" "$DOCS_DIR/reset-password.html"
rm -rf "$DOCS_DIR/vite-assets"

cp dist/login.html dist/reset-password.html "$DOCS_DIR/"
cp -r dist/vite-assets "$DOCS_DIR/vite-assets"

echo "Deployed frontend/dist/ -> docs/ (login.html, reset-password.html, vite-assets/)"
