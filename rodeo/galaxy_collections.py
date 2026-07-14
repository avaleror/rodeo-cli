"""Cache ansible-galaxy collection installs keyed on requirements.yml hash."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .paths import rodeo_dir

_MARKER = "ansible-collections.sha256"


def _marker_path() -> Path:
    return rodeo_dir() / _MARKER


def requirements_hash(req_file: Path) -> str:
    return hashlib.sha256(req_file.read_bytes()).hexdigest()


def collections_install_needed(req_file: Path) -> bool:
    marker = _marker_path()
    if not marker.exists():
        return True
    try:
        return marker.read_text().strip() != requirements_hash(req_file)
    except OSError:
        return True


def mark_collections_installed(req_file: Path) -> None:
    rodeo_dir().mkdir(parents=True, exist_ok=True)
    _marker_path().write_text(requirements_hash(req_file) + "\n")


def install_collections(req_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ansible-galaxy", "collection", "install", "-r", str(req_file)],
        capture_output=True,
        text=True,
    )


def ensure_collections(
    req_file: Path,
) -> tuple[bool, subprocess.CompletedProcess[str] | None]:
    """Install collections when requirements changed. Returns (ran_install, result)."""
    if not collections_install_needed(req_file):
        return False, None
    result = install_collections(req_file)
    if result.returncode == 0:
        mark_collections_installed(req_file)
    return True, result