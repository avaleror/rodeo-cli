#!/usr/bin/env bash
# install.sh — install rodeo-cli as a system command on SLES 16 / Leap 16
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
#   bash install.sh [--ref v0.7.0] [--dir /opt/rodeo-cli]
#
# After this script runs, type:  rodeo up
# That is all the user ever needs to know.

set -euo pipefail

RODEO_REPO="https://github.com/avaleror/rodeo-cli.git"
RODEO_REF="${RODEO_REF:-main}"
RODEO_DIR="${RODEO_DIR:-/opt/rodeo-cli}"
RODEO_BIN="/usr/local/bin/rodeo"

while [[ $# -gt 0 ]]; do
  case $1 in
    --ref) RODEO_REF="$2"; shift 2 ;;
    --dir) RODEO_DIR="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# ── 1. prereqs ────────────────────────────────────────────────────────────────
echo "==> Installing prerequisites"
if command -v zypper &>/dev/null; then
  zypper --non-interactive install --no-recommends -y python3 python3-pip git
elif command -v apt-get &>/dev/null; then
  apt-get install -y --no-install-recommends python3 python3-pip python3-venv git
elif command -v dnf &>/dev/null; then
  dnf install -y python3 python3-pip git
else
  echo "No supported package manager found (zypper / apt-get / dnf)" >&2
  exit 1
fi

# ── 2. clone or update ────────────────────────────────────────────────────────
if [[ -d "$RODEO_DIR/.git" ]]; then
  echo "==> Updating $RODEO_DIR"
  git -C "$RODEO_DIR" fetch --tags origin
  git -C "$RODEO_DIR" checkout "$RODEO_REF"
  git -C "$RODEO_DIR" pull --ff-only origin "$RODEO_REF" 2>/dev/null || true
else
  echo "==> Cloning rodeo-cli to $RODEO_DIR"
  git clone "$RODEO_REPO" "$RODEO_DIR"
  if [[ "$RODEO_REF" != "main" ]]; then
    git -C "$RODEO_DIR" checkout "$RODEO_REF"
  fi
fi

# ── 3. venv + install ─────────────────────────────────────────────────────────
# --system-site-packages exposes the SLES libvirt-python system binding to Ansible
echo "==> Setting up Python environment (this is internal — you will never need to touch it)"
python3 -m venv --system-site-packages "$RODEO_DIR/.venv"
"$RODEO_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$RODEO_DIR/.venv/bin/pip" install --quiet -e "$RODEO_DIR"

# ── 4. system symlink — the only thing the user ever sees ─────────────────────
echo "==> Linking rodeo to $RODEO_BIN"
ln -sf "$RODEO_DIR/.venv/bin/rodeo" "$RODEO_BIN"

# ── 5. done ───────────────────────────────────────────────────────────────────
VERSION=$("$RODEO_BIN" --version 2>/dev/null | awk '{print $NF}' || echo "installed")
echo ""
echo "  rodeo $VERSION is ready."
echo "  Run: rodeo up"
echo ""
