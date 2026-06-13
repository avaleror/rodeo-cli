#!/bin/bash
#
# scripts/bootstrap-sles.sh
#
# Purpose:
#   Provide a true one-command (curl | bash) entry point for operators on clean
#   SLES 16 / Leap 16 (or similar) hosts. The goal is minimal manual interaction:
#   no git clone, no manual venv, no manual pip, no export RODEO, no manual
#   mkdir + init.
#
# What it does (all hidden / automatic):
#   - Installs minimal prereqs via zypper (non-interactive).
#   - Clones the rodeo-cli source to an internal location (~/.rodeo-cli) so that
#     the user never has to perform a manual "git clone" of the tool itself.
#   - Creates a dedicated venv with --system-site-packages (required for the
#     SLES libvirt-python binding).
#   - pip installs the package in editable mode from the internal clone.
#   - Invokes the `rodeo bootstrap` subcommand, which in turn:
#       * creates the /usr/local/bin/rodeo symlink (via install-deps --link)
#         so that "rodeo" and "sudo rodeo" are first-class global commands.
#       * seeds a ready lab directory using the harvester-lab-config example
#         (the 2-node, 6 vCPU, no-Rancher variant).
#   - Prints the absolute minimal, copy-pasteable follow-up commands that use
#     the lab directory as the natural context (local rodeo-plan.yaml means
#     --config-dir is optional inside the dir).
#
# Usage (end users):
#   curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/scripts/bootstrap-sles.sh | bash
#
#   # Then run exactly the commands printed at the end.
#
# Flags (for power users / automation):
#   --force        Overwrite an existing internal clone and lab dir.
#   --lab-dir DIR  Choose a different location for the seeded lab (default: ~/harvester-rodeo-lab).
#
# Environment variables:
#   RODEO_DIR      Override the internal clone location (default: ~/.rodeo-cli).
#   LAB_DIR        Override the lab directory (same as --lab-dir).
#
# After successful run:
#   - /usr/local/bin/rodeo exists and points at the prepared venv.
#   - The chosen lab dir contains a complete declarative definition + artifacts
#     + rodeo-secrets.env (source it, then use sudo -E).
#   - Subsequent shells only need the link (or the venv on PATH) + the 4 lines
#     for the specific lab.
#
# Design notes:
#   - Clone step is *not* manual for the end user; the script owns it internally.
#   - The script deliberately leaves the internal ~/.rodeo-cli tree so that
#     future `rodeo` invocations (via the link) have a stable source tree for
#     --example resolution and future updates.
#   - All heavy lifting for host prep and declarative seeding is delegated to
#     existing first-class commands (install-deps --link and init --example).
#   - This script is the concrete realization of the "Option 1 – Polished Native"
#     path chosen for maximum simplicity on the target platform (SLES).
#
# See also:
#   - rodeo/commands/bootstrap_cmd.py (the subcommand invoked at the end)
#   - docs/user-guide.md and root README.md for the documented flows
#   - Generated content/ for the full rationale and alternatives considered

set -euo pipefail

DEFAULT_RODEO_DIR="$HOME/.rodeo-cli"
DEFAULT_LAB_DIR="$HOME/harvester-rodeo-lab"
FORCE=false
LAB_DIR=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --force) FORCE=true; shift ;;
    --lab-dir) LAB_DIR="$2"; shift 2 ;;
    --ref) RODEO_REF="$2"; shift 2 ;;
    *) echo "Unknown option $1"; exit 1 ;;
  esac
done

RODEO_DIR="${RODEO_DIR:-$DEFAULT_RODEO_DIR}"
LAB_DIR="${LAB_DIR:-$DEFAULT_LAB_DIR}"
RODEO_REF="${RODEO_REF:-main}"

echo "==> Installing minimal prereqs (python, git, etc.)"
sudo zypper --non-interactive install --no-recommends python3 python3-pip python3-virtualenv git

if [ -d "$RODEO_DIR" ] && [ "$FORCE" = false ]; then
  echo "==> $RODEO_DIR exists. Use --force to overwrite or set RODEO_DIR."
  exit 1
fi

if [ -d "$RODEO_DIR" ]; then
  echo "==> Removing existing $RODEO_DIR (force)"
  rm -rf "$RODEO_DIR"
fi

echo "==> Cloning rodeo-cli to $RODEO_DIR (internal, hidden from user)"
git clone --depth 1 https://github.com/avaleror/rodeo-cli.git "$RODEO_DIR"
if [ "$RODEO_REF" != "main" ]; then
  (cd "$RODEO_DIR" && git fetch --depth 1 origin "$RODEO_REF" || git fetch origin "$RODEO_REF" && git checkout FETCH_HEAD)
fi

echo "==> Setting up venv with --system-site-packages"
cd "$RODEO_DIR"
python3 -m venv --system-site-packages .venv
source .venv/bin/activate

echo "==> Installing rodeo-cli (editable)"
pip install --upgrade pip
pip install -e ".[dev]"

echo "==> Running rodeo bootstrap (link + lab seed)"
# Delegate to the first-class subcommand. It will:
#   - create /usr/local/bin/rodeo (if needed)
#   - run init --example harvester-lab-config into the requested lab dir
./.venv/bin/rodeo bootstrap --lab-dir "$LAB_DIR" --force

echo ""
echo "==> Bootstrap complete. Use these commands for clean interface:"
echo "  source $RODEO_DIR/.venv/bin/activate   # or add to PATH permanently"
echo "  # or rely on /usr/local/bin/rodeo link created by bootstrap"
echo "  cd $LAB_DIR"
echo "  source rodeo-secrets.env"
echo "  rodeo plan --config-dir ."
echo "  sudo -E rodeo deploy --config-dir . --check"
echo ""
echo "rodeo is now a clean command (thanks to the link). Lab uses the 2-node no-Rancher example."
echo "Re-run with --force if you want to reset."