#!/usr/bin/env bash
# install.sh: install rodeo-cli
#
# rodeo runs in one of two roles, and this script picks the right one:
#
#   kvm-host       SLES 16 / Leap 16. Runs labs on this machine. Needs root:
#                  installs python3/pip/git with the package manager, clones to
#                  /opt/rodeo-cli and links /usr/local/bin/rodeo.
#   control-plane  Any other Linux, or macOS. Drives remote hosts only
#                  (rodeo up --target aws, rodeo fleet ..., rodeo destroy --cloud),
#                  never runs a lab itself. No system packages: uses a Python
#                  3.10+ already on the machine, or a private one fetched with uv
#                  (the system Python is never touched), and installs the [aws]
#                  extra. As a normal user: ~/.local/share/rodeo-cli and
#                  ~/.local/bin/rodeo. As root: /opt/rodeo-cli and /usr/local/bin.
#
# Usage (production):
#   curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
#   bash install.sh [--ref v0.10.5] [--dir DIR] [--mode kvm-host|control-plane]
#
#   --mode           override the automatic choice (e.g. a KVM host on another
#                    distribution, at your own risk). Also: RODEO_MODE=<mode>.
#   RODEO_REPO=<git url>  clone/update from this repository instead of upstream
#   RODEO_REF=<ref>       default ref when --ref is not given (main)
#
# Usage (development):
#   bash install.sh --dev [--dir /path/to/rodeo-cli]
#
#   --dev  Install in editable mode with dev extras (pytest, ruff). Source
#          edits take effect immediately, no reinstall needed.
#
#          If the target directory already contains a checkout, git is never
#          touched — the existing tree is used as-is. If it doesn't exist yet,
#          the repo is cloned once from GitHub and then left for you to manage.
#
#          Defaults to the directory that contains this script when --dir is
#          not given, so running "bash install.sh --dev" from inside the repo
#          just works with zero git operations.
#
# Works with the bash 3.2 that macOS ships.

set -euo pipefail

RODEO_REPO="${RODEO_REPO:-https://github.com/avaleror/rodeo-cli.git}"
RODEO_REF="${RODEO_REF:-main}"
RODEO_MODE="${RODEO_MODE:-auto}"
DEV=0
RODEO_DIR=""

# Private Python for control-plane machines without a 3.10+ (pinned).
UV_VERSION="0.12.23"
UV_PYTHON="3.12"

# In --dev mode the default dir is the repo root that contains this script.
# ${BASH_SOURCE[0]:-$0} because the documented install path pipes this script
# (curl … | bash), where BASH_SOURCE is unset and set -u aborts the subshell.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo /)"

while [[ $# -gt 0 ]]; do
  case $1 in
    --ref)  RODEO_REF="$2"; shift 2 ;;
    --dir)  RODEO_DIR="$2"; shift 2 ;;
    --mode) RODEO_MODE="$2"; shift 2 ;;
    --dev)  DEV=1; shift ;;
    *)      echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# ── 0. role ───────────────────────────────────────────────────────────────────
# kvm-host only on SLES 16 / Leap 16 (what the Ansible roles and OVMF paths are
# written for); everything else is a control plane.
OS_NAME="$(uname -s)"
OS_PRETTY="$OS_NAME"
detect_mode() {
  if [[ "$OS_NAME" != "Linux" ]] || [[ ! -r /etc/os-release ]]; then
    echo control-plane
    return
  fi
  local id version_id major
  id="$(. /etc/os-release && echo "${ID:-}")"
  version_id="$(. /etc/os-release && echo "${VERSION_ID:-}")"
  major="${version_id%%.*}"
  case "$id" in
    sles|sles_sap|opensuse-leap)
      if [[ "$major" =~ ^[0-9]+$ ]] && [[ "$major" -ge 16 ]]; then
        echo kvm-host
        return
      fi
      ;;
  esac
  echo control-plane
}
if [[ "$OS_NAME" == "Linux" ]] && [[ -r /etc/os-release ]]; then
  OS_PRETTY="$(. /etc/os-release && echo "${PRETTY_NAME:-Linux}")"
elif [[ "$OS_NAME" == "Darwin" ]]; then
  OS_PRETTY="macOS $(sw_vers -productVersion 2>/dev/null || true)"
fi
case "$RODEO_MODE" in
  auto)                   RODEO_MODE="$(detect_mode)" ;;
  kvm-host|control-plane) ;;
  *) echo "Unknown --mode '$RODEO_MODE' (kvm-host | control-plane)" >&2; exit 1 ;;
esac

IS_ROOT=0
[[ "$(id -u)" -eq 0 ]] && IS_ROOT=1

if [[ "$RODEO_MODE" == "kvm-host" ]] || [[ $IS_ROOT -eq 1 ]]; then
  RODEO_BIN="/usr/local/bin/rodeo"
  DEFAULT_DIR="/opt/rodeo-cli"
else
  RODEO_BIN="$HOME/.local/bin/rodeo"
  DEFAULT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/rodeo-cli"
fi

# Apply directory default after flag parsing so --dir always wins.
if [[ -z "$RODEO_DIR" ]]; then
  if [[ $DEV -eq 1 ]]; then
    RODEO_DIR="$SCRIPT_DIR"
  else
    RODEO_DIR="$DEFAULT_DIR"
  fi
fi

echo "==> $OS_PRETTY: installing rodeo as a $RODEO_MODE"

# Decide upfront whether git is needed so the prereq install can include it.
# Production always needs git. Dev only needs it for the initial clone — once
# the checkout exists, git is never invoked again.
NEED_GIT=1
if [[ $DEV -eq 1 ]] && [[ -f "$RODEO_DIR/pyproject.toml" ]]; then
  NEED_GIT=0
fi

# ── 1. prereqs ────────────────────────────────────────────────────────────────
if [[ "$RODEO_MODE" == "kvm-host" ]]; then
  echo "==> Installing prerequisites"
  if command -v zypper &>/dev/null; then
    if [[ $NEED_GIT -eq 1 ]]; then
      zypper --non-interactive install --no-recommends -y python3 python3-pip git
    else
      zypper --non-interactive install --no-recommends -y python3 python3-pip
    fi
  elif command -v apt-get &>/dev/null; then
    if [[ $NEED_GIT -eq 1 ]]; then
      apt-get install -y --no-install-recommends python3 python3-pip python3-venv git
    else
      apt-get install -y --no-install-recommends python3 python3-pip python3-venv
    fi
  elif command -v dnf &>/dev/null; then
    if [[ $NEED_GIT -eq 1 ]]; then
      dnf install -y python3 python3-pip git
    else
      dnf install -y python3 python3-pip
    fi
  else
    echo "No supported package manager found (zypper / apt-get / dnf)" >&2
    exit 1
  fi
else
  # Control plane: only git (to clone) and curl (to fetch uv, if needed).
  # Installed with the package manager only when running as root; otherwise
  # say exactly what to install.
  missing=()
  if [[ $NEED_GIT -eq 1 ]] && ! command -v git &>/dev/null; then missing+=(git); fi
  if ! command -v curl &>/dev/null; then missing+=(curl); fi
  if [[ ${#missing[@]} -gt 0 ]]; then
    if [[ "$OS_NAME" == "Darwin" ]]; then
      echo "Missing: ${missing[*]}. Install the Xcode command line tools: xcode-select --install" >&2
      exit 1
    elif [[ $IS_ROOT -eq 1 ]] && command -v zypper &>/dev/null; then
      zypper --non-interactive install --no-recommends -y "${missing[@]}"
    elif [[ $IS_ROOT -eq 1 ]] && command -v apt-get &>/dev/null; then
      apt-get install -y --no-install-recommends "${missing[@]}"
    elif [[ $IS_ROOT -eq 1 ]] && command -v dnf &>/dev/null; then
      dnf install -y "${missing[@]}"
    else
      echo "Missing: ${missing[*]}. Install them with your package manager and re-run." >&2
      exit 1
    fi
  fi
fi

# ── 2. clone or update ────────────────────────────────────────────────────────
mkdir -p "$(dirname "$RODEO_DIR")"
if [[ $DEV -eq 1 ]]; then
  if [[ -f "$RODEO_DIR/pyproject.toml" ]]; then
    echo "==> Dev mode: using existing checkout at $RODEO_DIR (git untouched)"
  else
    echo "==> Dev mode: cloning $RODEO_REPO to $RODEO_DIR"
    git clone "$RODEO_REPO" "$RODEO_DIR"
    if [[ "$RODEO_REF" != "main" ]]; then
      git -C "$RODEO_DIR" checkout "$RODEO_REF"
    fi
    echo "==> Dev mode: initial clone done — git will not be touched on future runs"
  fi
else
  if [[ -d "$RODEO_DIR/.git" ]]; then
    echo "==> Updating $RODEO_DIR"
    # Self-heal the remote first. Hosts cloned single-branch (or pinned to a
    # since-deleted branch) otherwise strand here: fetch/pull only ever touch
    # that one branch and silently no-op, leaving the host on old code. Force a
    # normal wildcard refspec and the RODEO_REPO URL before fetching.
    git -C "$RODEO_DIR" remote set-url origin "$RODEO_REPO"
    git -C "$RODEO_DIR" config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
    # Fetch everything (all branches + tags), forcing tracking-ref updates.
    # No `|| true` — a failed update must be visible, not swallowed.
    git -C "$RODEO_DIR" fetch --tags --prune --force origin
    # Align deterministically to the requested ref. A branch is hard-reset to the
    # remote tip; a tag or SHA is checked out detached.
    if git -C "$RODEO_DIR" show-ref --verify --quiet "refs/remotes/origin/$RODEO_REF"; then
      git -C "$RODEO_DIR" checkout -B "$RODEO_REF" "origin/$RODEO_REF"
      git -C "$RODEO_DIR" reset --hard "origin/$RODEO_REF"
    else
      git -C "$RODEO_DIR" checkout --force "$RODEO_REF"
    fi
  else
    echo "==> Cloning rodeo-cli to $RODEO_DIR"
    git clone "$RODEO_REPO" "$RODEO_DIR"
    if [[ "$RODEO_REF" != "main" ]]; then
      git -C "$RODEO_DIR" checkout "$RODEO_REF"
    fi
  fi
fi

# ── 3. python ─────────────────────────────────────────────────────────────────
# A Python that rodeo can run on: 3.10+ with venv and ensurepip (Debian splits
# those into python3-venv).
py_ok() {
  "$1" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    >/dev/null 2>&1
}

if [[ "$RODEO_MODE" == "kvm-host" ]]; then
  PY="python3"
  if ! py_ok "$PY"; then
    echo "$(python3 --version 2>&1) is too old: rodeo needs Python 3.10+ (SLES 16 / Leap 16 ship it)." >&2
    exit 1
  fi
else
  PY=""
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    path="$(command -v "$candidate" 2>/dev/null || true)"
    if [[ -n "$path" ]] && py_ok "$path"; then
      PY="$path"
      break
    fi
  done
  if [[ -z "$PY" ]]; then
    # No suitable Python: a private one under $RODEO_DIR/.uv. Nothing else on
    # the machine changes (no PATH or shell profile edits).
    UV_HOME="$RODEO_DIR/.uv"
    echo "==> No Python 3.10+ found: fetching a private Python $UV_PYTHON with uv $UV_VERSION"
    if [[ ! -x "$UV_HOME/bin/uv" ]]; then
      curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" \
        | env UV_UNMANAGED_INSTALL="$UV_HOME/bin" UV_NO_MODIFY_PATH=1 sh >/dev/null
    fi
    export UV_PYTHON_INSTALL_DIR="$UV_HOME/python"
    "$UV_HOME/bin/uv" python install --quiet "$UV_PYTHON"
    PY="$("$UV_HOME/bin/uv" python find "$UV_PYTHON")"
  fi
  echo "==> Using $("$PY" --version 2>&1) ($PY)"
fi

# ── 4. venv + install ─────────────────────────────────────────────────────────
# kvm-host: --system-site-packages exposes the SLES libvirt-python system binding
# to Ansible. control-plane: an isolated venv with the [aws] extra (boto3).
# pip install -e creates an editable (symlinked) install in both modes — source
# changes are reflected immediately without reinstalling.
echo "==> Setting up Python environment (this is internal — you will never need to touch it)"
VENV="$RODEO_DIR/.venv"
# A venv left by an earlier run on a Python that is too old (or gone) is rebuilt.
if [[ -e "$VENV" ]] && ! py_ok "$VENV/bin/python"; then
  echo "==> Replacing the existing environment (its Python is missing or older than 3.10)"
  rm -rf "$VENV"
fi
if [[ "$RODEO_MODE" == "kvm-host" ]]; then
  "$PY" -m venv --system-site-packages "$VENV"
  EXTRAS=""
  if [[ $DEV -eq 1 ]]; then EXTRAS="[dev]"; fi
else
  "$PY" -m venv "$VENV"
  EXTRAS="[aws]"
  if [[ $DEV -eq 1 ]]; then EXTRAS="[dev,aws]"; fi
fi
"$VENV/bin/pip" install --quiet --upgrade pip
if [[ $DEV -eq 1 ]]; then
  # GIT_DIR='' prevents setuptools from invoking git for version detection.
  GIT_DIR='' "$VENV/bin/pip" install --quiet -e "$RODEO_DIR$EXTRAS"
else
  "$VENV/bin/pip" install --quiet -e "$RODEO_DIR$EXTRAS"
fi

# ── 5. command link — the only thing the user ever sees ───────────────────────
echo "==> Linking rodeo to $RODEO_BIN"
mkdir -p "$(dirname "$RODEO_BIN")"
ln -sf "$VENV/bin/rodeo" "$RODEO_BIN"

# ── 6. done ───────────────────────────────────────────────────────────────────
VERSION=$("$RODEO_BIN" --version 2>/dev/null | awk '{print $NF}' || echo "installed")
echo ""
if [[ $DEV -eq 1 ]]; then
  echo "  rodeo $VERSION is ready  [dev, $RODEO_MODE: $RODEO_DIR]"
  echo "  Source edits apply immediately. Run the test suite:"
  echo "    cd $RODEO_DIR && .venv/bin/pytest tests/ -v"
  echo "    .venv/bin/ruff check rodeo tests"
else
  echo "  rodeo $VERSION is ready ($RODEO_MODE)."
fi
if [[ "$RODEO_MODE" == "kvm-host" ]]; then
  echo "  Run: rodeo up"
else
  echo "  This machine drives remote hosts; labs run on them, not here."
  echo "  Run: rodeo up --profile <name> --target aws     (rodeo profiles lists them)"
  BIN_DIR="$(dirname "$RODEO_BIN")"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
      echo ""
      case "$(basename "${SHELL:-}")" in
        zsh)  RC="~/.zshrc" ;;
        bash) RC="~/.bashrc" ;;
        *)    RC="~/.profile" ;;
      esac
      echo "  $BIN_DIR is not on your PATH yet. Add it, then open a new shell:"
      echo "    echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> $RC"
      ;;
  esac
fi
echo ""
