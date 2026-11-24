"""install.sh installs the latest *validated* release by default.

The validated release is the tag in STABLE on main (docs/releasing.md). These
tests run install.sh's own resolve_stable_ref against real git repositories,
so the bash that ships is the bash under test.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "install.sh"


def _function(name: str) -> str:
    text = INSTALL_SH.read_text()
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    assert m, f"{name} not found in install.sh"
    return m.group(0)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _origin(tmp_path: Path, *, tags: list[str], stable: str | None) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    for tag in tags:
        (origin / "f").write_text(tag)
        _git(origin, "add", "f")
        _git(origin, "commit", "-q", "-m", tag)
        _git(origin, "tag", tag)
    if stable is not None:
        (origin / "STABLE").write_text(stable)
        _git(origin, "add", "STABLE")
        _git(origin, "commit", "-q", "-m", "stable")
    else:
        _git(origin, "commit", "-q", "--allow-empty", "-m", "head")
    return origin


def _resolve(tmp_path: Path, origin: Path) -> subprocess.CompletedProcess:
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    script = _function("resolve_stable_ref") + f'resolve_stable_ref "{clone}"\n'
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + script],
                          capture_output=True, text=True)


def test_default_ref_is_stable():
    assert 'RODEO_REF="${RODEO_REF:-stable}"' in INSTALL_SH.read_text()


def test_stable_file_wins_over_a_newer_tag(tmp_path):
    origin = _origin(tmp_path, tags=["v0.32.1", "v0.32.2", "v0.33.0"], stable="v0.32.2\n")
    r = _resolve(tmp_path, origin)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "v0.32.2"


@pytest.mark.parametrize("stable", [None, "v9.9.9\n", "main\n", "v0.33.0; rm -rf /\n"])
def test_missing_or_bad_stable_falls_back_to_the_newest_tag(tmp_path, stable):
    origin = _origin(tmp_path, tags=["v0.9.0", "v0.10.0", "v0.32.2"], stable=stable)
    r = _resolve(tmp_path, origin)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "v0.32.2"
    assert "newest release" in r.stderr


def test_no_tags_at_all_falls_back_to_main(tmp_path):
    origin = _origin(tmp_path, tags=[], stable=None)
    r = _resolve(tmp_path, origin)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "main"


def test_repo_stable_names_an_existing_release():
    """STABLE must hold one vX.Y.Z tag; CI checkouts have no tags, so only the shape."""
    value = (ROOT / "STABLE").read_text().strip()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", value), value
