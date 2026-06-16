#!/usr/bin/env bash
# Deprecated — use install.sh at the repo root instead.
#
#   curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
#
# This script is kept for backwards compatibility and forwards to install.sh.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/../install.sh" "$@"
