#!/usr/bin/env bash
# Build the offline PDF guide for a locale (default: pt-BR).
#
# Usage: scripts/offline-guide/build.sh [locale]
set -euo pipefail

LOCALE="${1:-pt-BR}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

MD="$REPO/offline-guides/.build/guide-$LOCALE.md"
PDF="$REPO/offline-guides/rodeo-$LOCALE-guia-offline.pdf"

python3 "$HERE/build_guide.py" --locale "$LOCALE" --out "$MD"
python3 "$HERE/render_pdf.py" --md "$MD" --out "$PDF" --locale "$LOCALE"
