#!/usr/bin/env bash
# Wire the repo's .githooks/ directory as the active hook path for this clone.
#
# Run once after cloning:
#   bash scripts/install-hooks.sh
#
# What this does:
#   - pre-push  : blocks push if ruff finds any warnings
#   - pre-commit: chains to the global pre-commit (gitleaks, etc.) so it keeps running
#
# core.hooksPath is set locally (in .git/config) — it does not affect other repos.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

git config core.hooksPath .githooks
echo "Hooks installed. Active hooks:"
echo "  pre-commit : chains global hook (gitleaks)"
echo "  pre-push   : ruff check rodeo tests"
echo ""
echo "To bypass in an emergency: git push --no-verify"
