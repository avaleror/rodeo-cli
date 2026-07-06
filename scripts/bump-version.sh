#!/usr/bin/env bash
# Bump rodeo-cli to a new version, update every reference, commit, and tag.
#
# Usage: ./scripts/bump-version.sh 0.11.0
#
# What it touches:
#   pyproject.toml          — authoritative source; importlib.metadata reads from here
#   README.md               — **Version:** badge
#   CONTEXT.md              — **Version:** badge
#   docs/architecture.md    — **Version:** badge
#   install.sh              — example --ref in the usage header
#   CLAUDE.md               — "Current state" section (gitignored; updated if present)
#
# After running, push both the commit and the tag:
#   git push && git push origin v<NEW>
set -euo pipefail

NEW="${1:-}"
if [[ -z "$NEW" ]]; then
    echo "Usage: $0 <new-version>   e.g. $0 0.11.0" >&2
    exit 1
fi

# Strip leading 'v' so we always work with bare semver.
NEW="${NEW#v}"
TAG="v${NEW}"

# Derive current version from pyproject.toml.
OLD=$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
if [[ -z "$OLD" ]]; then
    echo "Could not read current version from pyproject.toml" >&2
    exit 1
fi

if [[ "$OLD" == "$NEW" ]]; then
    echo "Already at $NEW — nothing to do." >&2
    exit 0
fi

echo "Bumping $OLD → $NEW"

# 1. pyproject.toml — the single source of truth.
sed -i.bak "s/^version = \"${OLD}\"$/version = \"${NEW}\"/" pyproject.toml
rm -f pyproject.toml.bak

# 2. Doc **Version:** badges — replace the semver after the badge label, keeping
#    any trailing text (e.g. the parenthetical in CONTEXT.md).
for f in README.md CONTEXT.md docs/architecture.md; do
    [[ -f "$f" ]] || continue
    sed -i.bak -E "s/(\*\*Version:\*\* )[0-9]+\.[0-9]+\.[0-9]+/\1${NEW}/" "$f"
    rm -f "${f}.bak"
done

# 3. install.sh — the example --ref in the usage header.
sed -i.bak -E "s/(--ref v)[0-9]+\.[0-9]+\.[0-9]+/\1${NEW}/" install.sh
rm -f install.sh.bak

# 4. CLAUDE.md — gitignored but update it if present on this machine.
if [[ -f CLAUDE.md ]]; then
    sed -i.bak "s/v${OLD}/v${NEW}/g" CLAUDE.md
    rm -f CLAUDE.md.bak
fi

# Verify: scan tracked files for the old version string; fail if any remain.
# (Historical changelog entries like "v0.3" are shorter than X.Y.Z, so they
# won't match a full-semver OLD and are correctly left alone.)
MISSED=$(git grep -l "${OLD}" -- ':!*.egg-info' 2>/dev/null || true)
if [[ -n "$MISSED" ]]; then
    echo ""
    echo "⚠  Old version string '${OLD}' still found in tracked files:" >&2
    echo "$MISSED" >&2
    echo "Fix those files, then re-run or commit manually." >&2
    exit 1
fi

# Commit and tag.
git add pyproject.toml README.md CONTEXT.md docs/architecture.md install.sh
git commit -m "chore: bump version to ${NEW}"
git tag -a "${TAG}" -m "rodeo-cli ${NEW}"

echo ""
echo "Done. Push with:"
echo "  git push && git push origin ${TAG}"
