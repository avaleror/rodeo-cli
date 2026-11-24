#!/usr/bin/env bash
# promote-stable.sh: open the PR that makes a validated release the default.
#
#   scripts/promote-stable.sh v0.33.0 smoke-v0.33.0-rancher-test.md [more.md ...]
#
# Writes <tag> to STABLE on a new branch off origin/main and opens a PR whose
# body is the evidence files (from scripts/smoke-aws.sh). Merging that PR is
# the promotion: from then on install.sh and `rodeo self-update` install <tag>,
# and the release workflow puts GitHub's "Latest" badge on it.
# It never merges anything. Rolling back is reverting the PR.

set -euo pipefail

TAG="${1:-}"
shift || true
if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || [[ $# -eq 0 ]]; then
  echo "usage: $0 vX.Y.Z evidence.md [more.md ...]" >&2
  exit 2
fi
for f in "$@"; do
  [[ -f "$f" ]] || { echo "no such evidence file: $f" >&2; exit 2; }
  grep -q ": PASS" "$f" || { echo "$f does not record a PASS: not promoting" >&2; exit 1; }
done

ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" fetch -q --tags origin main
git -C "$ROOT" rev-parse -q --verify "refs/tags/${TAG}" >/dev/null \
  || { echo "tag ${TAG} does not exist on origin" >&2; exit 1; }
gh release view "$TAG" >/dev/null || { echo "no GitHub release for ${TAG}" >&2; exit 1; }
CURRENT="$(git -C "$ROOT" show origin/main:STABLE 2>/dev/null | tr -d '[:space:]' || true)"
[[ "$CURRENT" != "$TAG" ]] || { echo "STABLE is already ${TAG}" >&2; exit 1; }

BRANCH="chore/stable-${TAG}"
WT="$(mktemp -d)"
trap 'git -C "$ROOT" worktree remove --force "$WT" >/dev/null 2>&1 || true' EXIT
git -C "$ROOT" worktree add -q -b "$BRANCH" "$WT" origin/main
printf '%s\n' "$TAG" >"$WT/STABLE"
git -C "$WT" add STABLE
git -C "$WT" commit -q -m "chore(stable): promote ${TAG}

${CURRENT:-none} -> ${TAG}. Evidence in the PR."
git -C "$WT" push -q -u origin "$BRANCH"

BODY="$(mktemp)"
{
  echo "Makes **${TAG}** the release that \`install.sh\` and \`rodeo self-update\` install by default (was ${CURRENT:-none})."
  echo
  echo "Checklist (docs/releasing.md):"
  echo "- [x] CI green on the release"
  echo "- [x] live smoke on AWS (below)"
  echo "- [ ] profiles or workshops this release changed, deployed and checked"
  echo
  cat "$@"
} >"$BODY"
gh pr create --head "$BRANCH" --title "chore(stable): promote ${TAG}" --body-file "$BODY"
rm -f "$BODY"
