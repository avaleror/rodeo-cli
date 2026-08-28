"""Story-feature dependencies: rmstory + multilang as distro packages.

rmstory (github.com/rmahique/rmstory-lib) and its translation store
multilang-lib (github.com/rmahique/multilang-lib) are deliberately NOT
consumed from PyPI: both projects attach .deb/.rpm packages for the supported
distros to their GitHub releases, and rodeo depends on those system packages —
installed by ``sudo rodeo install-deps --story`` — the same way libvirt-python
is a system dependency.

The two packaging matrices don't fully overlap (rmstory builds leap-16 +
sles-15-sp7; multilang's Python binding builds sles-16 + leap-15). All are
noarch Python packages, so the candidate lists below fall back to the nearest
family variant; the installer reports which asset was actually used.

At runtime, story features import rmstory lazily: call :func:`require_rmstory`
first, which fails closed with the install hint when the package is absent.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from .config import ConfigError

# Pinned release tags — bump deliberately, together with the prefix tables
# below if the projects' packaging matrices change.
RMSTORY_RELEASE = "1.1.2"
MULTILANG_RELEASE = "1.1.2"

_GITHUB_OWNER = "rmahique"

_INSTALL_HINT = (
    "story features need the rmstory + multilang system packages — "
    "install them with: sudo rodeo install-deps --story"
)

# Names that identify the Python packages among each release's many assets.
_RMSTORY_NEEDLE = "python3-rmstory"
_MULTILANG_NEEDLE = "python3-multilang"

_EXCLUDE_SUFFIXES = (".sha512", ".src.rpm")
_EXCLUDE_SUBSTRINGS = ("-dbgsym", "-debuginfo", "-debugsource", "-devel")


def rmstory_available() -> bool:
    return importlib.util.find_spec("rmstory") is not None


def require_rmstory():
    """Import and return the rmstory module, or fail with the install hint."""
    if not rmstory_available():
        raise ConfigError(
            f"rmstory is not installed — {_INSTALL_HINT}\n"
            "If rodeo runs in a venv, recreate it with --system-site-packages "
            "so the system package is visible (same as libvirt-python)."
        )
    import rmstory

    return rmstory


def _os_release(path: str | Path = "/etc/os-release") -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for line in Path(path).read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip().strip('"')
    except OSError:
        pass
    return data


def _suse_variant(osr: dict[str, str]) -> str:
    """'tumbleweed' | '16' (Leap/SLES 16) | 'sles-15' | 'leap-15'."""
    ident = (osr.get("ID") or "").lower()
    version = osr.get("VERSION_ID") or ""
    major = version.split(".")[0] if version else ""
    if "tumbleweed" in ident:
        return "tumbleweed"
    if major == "15":
        return "sles-15" if ident == "sles" else "leap-15"
    return "16"


def _candidate_prefixes(family: str, variant: str = "") -> dict[str, list[str]]:
    """Ordered asset-name prefixes per package for this host, best match first."""
    if family == "debian":
        return {
            "rmstory": ["debian-bookworm-"],
            "multilang": ["python-debian-bookworm-"],
        }
    if family == "fedora":
        return {
            "rmstory": ["fedora-latest-"],
            "multilang": ["python-fedora-latest-"],
        }
    if family == "suse":
        if variant == "leap-15":
            # rmstory documents Leap 15 as unsupported (its Python stack is too old).
            raise ConfigError(
                "openSUSE Leap 15 is not supported by rmstory — use Leap 16, "
                "SLES, or Tumbleweed for the story features"
            )
        if variant == "tumbleweed":
            return {
                "rmstory": ["opensuse-tumbleweed-", "opensuse-leap-16-"],
                "multilang": ["python-opensuse-tumbleweed-", "python-sles-16-"],
            }
        if variant == "sles-15":
            return {
                "rmstory": ["sles-15-sp7-", "opensuse-leap-16-"],
                "multilang": ["python-opensuse-leap-15-", "python-sles-16-"],
            }
        # Leap 16 / SLES 16 / newer.
        return {
            "rmstory": ["opensuse-leap-16-", "sles-15-sp7-", "opensuse-tumbleweed-"],
            "multilang": ["python-sles-16-", "python-opensuse-tumbleweed-"],
        }
    raise ConfigError(
        f"cannot install story packages: unsupported or undetected distro ({family!r})"
    )


def _release_assets(repo: str, tag: str) -> list[dict]:
    url = f"https://api.github.com/repos/{_GITHUB_OWNER}/{repo}/releases/tags/{tag}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
    except Exception as exc:
        raise ConfigError(f"could not read the {repo} {tag} release from GitHub: {exc}")
    return data.get("assets", [])


def _pick_asset(assets: list[dict], prefixes: list[str], needle: str) -> dict:
    """First binary package matching the highest-priority prefix."""
    for prefix in prefixes:
        for asset in assets:
            name = asset.get("name", "")
            if not name.startswith(prefix):
                continue
            if needle not in name:
                continue
            if name.endswith(_EXCLUDE_SUFFIXES):
                continue
            if any(s in name for s in _EXCLUDE_SUBSTRINGS):
                continue
            if not name.endswith((".deb", ".rpm")):
                continue
            return asset
    raise ConfigError(
        f"no {needle} package for this distro in the release "
        f"(looked for {', '.join(prefixes)}) — check the project's packaging matrix"
    )


def _download_verified(asset: dict, assets: list[dict], dest_dir: Path) -> Path:
    """Download one asset; verify it against its .sha512 sibling when present."""
    dest = dest_dir / asset["name"]
    urllib.request.urlretrieve(asset["browser_download_url"], dest)
    sha_name = asset["name"] + ".sha512"
    sha_asset = next((a for a in assets if a.get("name") == sha_name), None)
    if sha_asset is not None:
        with urllib.request.urlopen(sha_asset["browser_download_url"], timeout=30) as r:
            expected = r.read().decode().split()[0].strip().lower()
        actual = hashlib.sha512(dest.read_bytes()).hexdigest()
        if actual != expected:
            raise ConfigError(
                f"sha512 mismatch for {asset['name']} — refusing to install"
            )
    return dest


def _install_cmd(family: str, paths: list[str]) -> list[str]:
    if family == "suse":
        # Release assets are unsigned; integrity comes from the sha512 check.
        return ["zypper", "--non-interactive", "install", "--allow-unsigned-rpm", *paths]
    if family == "debian":
        return ["apt-get", "install", "-y", *paths]
    return ["dnf", "install", "-y", *paths]


def install_story_deps(family: str, run=subprocess.run) -> list[str]:
    """Install rmstory + multilang Python packages from their GitHub releases.

    ``family`` is install_deps._detect_distro()'s result. Both packages go in
    one package-manager transaction so the rmstory→multilang dependency
    resolves regardless of order. Returns the installed asset names.
    """
    variant = _suse_variant(_os_release()) if family == "suse" else ""
    prefixes = _candidate_prefixes(family, variant)

    multilang_assets = _release_assets("multilang-lib", MULTILANG_RELEASE)
    rmstory_assets = _release_assets("rmstory-lib", RMSTORY_RELEASE)
    picked = [
        (_pick_asset(multilang_assets, prefixes["multilang"], _MULTILANG_NEEDLE), multilang_assets),
        (_pick_asset(rmstory_assets, prefixes["rmstory"], _RMSTORY_NEEDLE), rmstory_assets),
    ]

    with tempfile.TemporaryDirectory(prefix="rodeo-story-") as tmp:
        tmp_path = Path(tmp)
        paths = [str(_download_verified(a, assets, tmp_path)) for a, assets in picked]
        result = run(_install_cmd(family, paths), check=False)
        if result.returncode != 0:
            raise ConfigError(
                f"story package install failed (exit {result.returncode}) — "
                f"packages: {', '.join(a['name'] for a, _ in picked)}"
            )
    return [a["name"] for a, _ in picked]
