#!/usr/bin/env bash
# install.sh — install rodeo-cli as a system command on SLES 16 / Leap 16
#
# Usage (production):
#   curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
#   bash install.sh [--ref v0.7.0] [--dir /opt/rodeo-cli]
#
# Usage (development):
#   bash install.sh --dev [--dir /path/to/rodeo-cli]
#
#   --dev  Skip git clone/update entirely. Use an existing local checkout.
#          The venv is created inside the repo directory and the package is
#          installed in editable mode — source edits take effect immediately,
#          no reinstall needed. Dev extras (pytest, ruff) are also installed.
#          Defaults to the directory that contains this script when --dir is
#          not given, so you can run "bash install.sh --dev" from inside the
#          repo and it just works.
#
# After this script runs, type:  rodeo up
# That is all the user ever needs to know.

set -euo pipefail

RODEO_REPO="https://github.com/avaleror/rodeo-cli.git"
RODEO_REF="${RODEO_REF:-main}"
RODEO_BIN="/usr/local/bin/rodeo"
DEV=0
RODEO_DIR=""

# In --dev mode the default dir is the repo root that contains this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [[ $# -gt 0 ]]; do
  case $1 in
    --ref)  RODEO_REF="$2"; shift 2 ;;
    --dir)  RODEO_DIR="$2"; shift 2 ;;
    --dev)  DEV=1; shift ;;
    *)      echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# Apply directory default after flag parsing so --dir always wins.
if [[ -z "$RODEO_DIR" ]]; then
  if [[ $DEV -eq 1 ]]; then
    RODEO_DIR="$SCRIPT_DIR"
  else
    RODEO_DIR="/opt/rodeo-cli"
  fi
fi

# ── 1. prereqs ────────────────────────────────────────────────────────────────
# Dev mode only needs Python — git is already present (you cloned the repo).
echo "==> Installing prerequisites"
if command -v zypper &>/dev/null; then
  if [[ $DEV -eq 1 ]]; then
    zypper --non-interactive install --no-recommends -y python3 python3-pip
  else
    zypper --non-interactive install --no-recommends -y python3 python3-pip git
  fi
elif command -v apt-get &>/dev/null; then
  if [[ $DEV -eq 1 ]]; then
    apt-get install -y --no-install-recommends python3 python3-pip python3-venv
  else
    apt-get install -y --no-install-recommends python3 python3-pip python3-venv git
  fi
elif command -v dnf &>/dev/null; then
  if [[ $DEV -eq 1 ]]; then
    dnf install -y python3 python3-pip
  else
    dnf install -y python3 python3-pip git
  fi
else
  echo "No supported package manager found (zypper / apt-get / dnf)" >&2
  exit 1
fi

# ── 2. clone or update ────────────────────────────────────────────────────────
if [[ $DEV -eq 1 ]]; then
  echo "==> Dev mode: using local checkout at $RODEO_DIR (git untouched)"
  if [[ ! -f "$RODEO_DIR/pyproject.toml" ]]; then
    echo "Error: $RODEO_DIR does not look like a rodeo-cli checkout (no pyproject.toml)." >&2
    echo "       Pass --dir /path/to/rodeo-cli to point at the right directory." >&2
    exit 1
  fi
else
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
fi

# ── 3. venv + install ─────────────────────────────────────────────────────────
# --system-site-packages exposes the SLES libvirt-python system binding to Ansible.
# pip install -e creates an editable (symlinked) install in both modes — source
# changes are reflected immediately without reinstalling.
echo "==> Setting up Python environment (this is internal — you will never need to touch it)"
python3 -m venv --system-site-packages "$RODEO_DIR/.venv"
"$RODEO_DIR/.venv/bin/pip" install --quiet --upgrade pip
if [[ $DEV -eq 1 ]]; then
  # GIT_DIR='' prevents setuptools from invoking git for version detection.
  GIT_DIR='' "$RODEO_DIR/.venv/bin/pip" install --quiet -e "$RODEO_DIR[dev]"
else
  "$RODEO_DIR/.venv/bin/pip" install --quiet -e "$RODEO_DIR"
fi

# ── 4. system symlink — the only thing the user ever sees ─────────────────────
echo "==> Linking rodeo to $RODEO_BIN"
ln -sf "$RODEO_DIR/.venv/bin/rodeo" "$RODEO_BIN"

# ── 5. done ───────────────────────────────────────────────────────────────────
VERSION=$("$RODEO_BIN" --version 2>/dev/null | awk '{print $NF}' || echo "installed")
echo ""
if [[ $DEV -eq 1 ]]; then
  echo "  rodeo $VERSION is ready  [dev — $RODEO_DIR]"
  echo "  Source edits apply immediately. Run the test suite:"
  echo "    cd $RODEO_DIR && .venv/bin/pytest tests/ -v"
  echo "    .venv/bin/ruff check rodeo tests"
else
  echo "  rodeo $VERSION is ready."
fi
echo "  Run: rodeo up"
echo ""
