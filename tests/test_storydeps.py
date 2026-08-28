"""Story dependencies: rmstory + multilang installed as distro packages, not PyPI.

Pins the asset-selection contract against the real release layouts of
rmahique/rmstory-lib and rmahique/multilang-lib (tag 1.1.2): rmstory ships
debian-bookworm / fedora-latest / leap-16 / tumbleweed / sles-15-sp7 noarch
packages; multilang's Python binding ships python-<distro> assets with
sles-16 + leap-15 on the SUSE side. No network is touched here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rodeo import storydeps
from rodeo.config import ConfigError
from rodeo.storydeps import (
    _candidate_prefixes,
    _install_cmd,
    _pick_asset,
    _suse_variant,
    install_story_deps,
    require_rmstory,
    rmstory_available,
)


def _assets(*names: str) -> list[dict]:
    return [{"name": n, "browser_download_url": f"https://example.invalid/{n}"} for n in names]


_RMSTORY_ASSETS = _assets(
    "debian-bookworm-python3-rmstory_0.1.0+20260825-1_all.deb",
    "debian-bookworm-python3-rmstory_0.1.0+20260825-1_all.deb.sha512",
    "fedora-latest-python3-rmstory-0.1.0.20260825-1.fc44.noarch.rpm",
    "fedora-latest-python3-rmstory-0.1.0.20260825-1.fc44.src.rpm",
    "opensuse-leap-16-python3-rmstory-0.1.0.20260825-1.noarch.rpm",
    "opensuse-tumbleweed-python3-rmstory-0.1.0.20260825-1.noarch.rpm",
    "sles-15-sp7-python3-rmstory-0.1.0.20260825-1.noarch.rpm",
)

_MULTILANG_ASSETS = _assets(
    "c-sles-16-libmultilang-1.1.2-1.x86_64.rpm",
    "python-debian-bookworm-python3-multilang_1.1.2-1_all.deb",
    "python-fedora-latest-python3-multilang-1.1.2-1.fc44.noarch.rpm",
    "python-fedora-latest-python3-multilang-1.1.2-1.fc44.src.rpm",
    "python-opensuse-leap-15-python3-multilang-1.1.2-1.noarch.rpm",
    "python-opensuse-tumbleweed-python3-multilang-1.1.2-1.noarch.rpm",
    "python-sles-16-python3-multilang-1.1.2-1.noarch.rpm",
    "python-sles-16-python3-multilang-1.1.2-1.src.rpm",
)


# ---------- variant + prefix selection ----------

def test_suse_variant_detection():
    assert _suse_variant({"ID": "opensuse-tumbleweed"}) == "tumbleweed"
    assert _suse_variant({"ID": "sles", "VERSION_ID": "15.7"}) == "sles-15"
    assert _suse_variant({"ID": "opensuse-leap", "VERSION_ID": "15.6"}) == "leap-15"
    assert _suse_variant({"ID": "opensuse-leap", "VERSION_ID": "16.0"}) == "16"
    assert _suse_variant({"ID": "sles", "VERSION_ID": "16.0"}) == "16"


def test_sles16_picks_leap16_rmstory_and_sles16_multilang():
    prefixes = _candidate_prefixes("suse", "16")
    rm = _pick_asset(_RMSTORY_ASSETS, prefixes["rmstory"], "python3-rmstory")
    ml = _pick_asset(_MULTILANG_ASSETS, prefixes["multilang"], "python3-multilang")
    assert rm["name"].startswith("opensuse-leap-16-")
    assert ml["name"].startswith("python-sles-16-")
    assert ml["name"].endswith(".noarch.rpm")


def test_sles15_picks_its_own_rmstory_build():
    prefixes = _candidate_prefixes("suse", "sles-15")
    rm = _pick_asset(_RMSTORY_ASSETS, prefixes["rmstory"], "python3-rmstory")
    ml = _pick_asset(_MULTILANG_ASSETS, prefixes["multilang"], "python3-multilang")
    assert rm["name"].startswith("sles-15-sp7-")
    assert ml["name"].startswith("python-opensuse-leap-15-")


def test_debian_and_fedora_pick_their_packages():
    for family, rm_prefix, ml_prefix in (
        ("debian", "debian-bookworm-", "python-debian-bookworm-"),
        ("fedora", "fedora-latest-", "python-fedora-latest-"),
    ):
        prefixes = _candidate_prefixes(family)
        rm = _pick_asset(_RMSTORY_ASSETS, prefixes["rmstory"], "python3-rmstory")
        ml = _pick_asset(_MULTILANG_ASSETS, prefixes["multilang"], "python3-multilang")
        assert rm["name"].startswith(rm_prefix)
        assert ml["name"].startswith(ml_prefix)


def test_leap15_is_rejected_for_rmstory():
    with pytest.raises(ConfigError, match="Leap 15"):
        _candidate_prefixes("suse", "leap-15")


def test_unknown_family_is_rejected():
    with pytest.raises(ConfigError, match="unsupported"):
        _candidate_prefixes("unknown")


def test_pick_asset_skips_checksums_sources_and_debug_variants():
    assets = _assets(
        "python-sles-16-python3-multilang-1.1.2-1.src.rpm",
        "python-sles-16-python3-multilang-1.1.2-1.noarch.rpm.sha512",
        "python-sles-16-python3-multilang-devel-1.1.2-1.noarch.rpm",
        "python-sles-16-python3-multilang-1.1.2-1.noarch.rpm",
    )
    picked = _pick_asset(assets, ["python-sles-16-"], "python3-multilang")
    assert picked["name"] == "python-sles-16-python3-multilang-1.1.2-1.noarch.rpm"


def test_pick_asset_fails_clearly_when_nothing_matches():
    with pytest.raises(ConfigError, match="no python3-rmstory package"):
        _pick_asset(_MULTILANG_ASSETS, ["opensuse-leap-16-"], "python3-rmstory")


# ---------- runtime guard ----------

def test_require_rmstory_raises_with_install_hint(monkeypatch):
    monkeypatch.setattr(storydeps.importlib.util, "find_spec", lambda name: None)
    assert rmstory_available() is False
    with pytest.raises(ConfigError, match="rodeo install-deps --story"):
        require_rmstory()


# ---------- install transaction ----------

def _fake_download(asset, assets, dest_dir):
    path = dest_dir / asset["name"]
    path.write_bytes(b"pkg")
    return path


def test_install_story_deps_single_transaction(monkeypatch):
    monkeypatch.setattr(
        storydeps, "_release_assets",
        lambda repo, tag: _MULTILANG_ASSETS if repo == "multilang-lib" else _RMSTORY_ASSETS,
    )
    monkeypatch.setattr(storydeps, "_download_verified", _fake_download)
    monkeypatch.setattr(storydeps, "_os_release", lambda: {"ID": "sles", "VERSION_ID": "16.0"})

    calls = []

    def fake_run(cmd, check=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    names = install_story_deps("suse", run=fake_run)
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:3] == ["zypper", "--non-interactive", "install"]
    assert "--allow-unsigned-rpm" in cmd
    # Both packages in the one transaction, multilang first is not required —
    # the package manager resolves the dependency between them.
    assert sum("python3-multilang" in c for c in cmd) == 1
    assert sum("python3-rmstory" in c for c in cmd) == 1
    assert names == [
        "python-sles-16-python3-multilang-1.1.2-1.noarch.rpm",
        "opensuse-leap-16-python3-rmstory-0.1.0.20260825-1.noarch.rpm",
    ]


def test_install_story_deps_fails_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        storydeps, "_release_assets",
        lambda repo, tag: _MULTILANG_ASSETS if repo == "multilang-lib" else _RMSTORY_ASSETS,
    )
    monkeypatch.setattr(storydeps, "_download_verified", _fake_download)
    monkeypatch.setattr(storydeps, "_os_release", lambda: {"ID": "debian"})

    def fake_run(cmd, check=False):
        return subprocess.CompletedProcess(cmd, 4)

    with pytest.raises(ConfigError, match=r"exit 4"):
        install_story_deps("debian", run=fake_run)


def test_install_cmd_per_family():
    assert _install_cmd("debian", ["/tmp/a.deb"])[:3] == ["apt-get", "install", "-y"]
    assert _install_cmd("fedora", ["/tmp/a.rpm"])[:3] == ["dnf", "install", "-y"]


def test_download_verified_checks_sha512(monkeypatch, tmp_path):
    import hashlib

    payload = b"package-bytes"
    good = hashlib.sha512(payload).hexdigest()

    asset = {"name": "pkg.rpm", "browser_download_url": "https://example.invalid/pkg.rpm"}
    sha = {"name": "pkg.rpm.sha512", "browser_download_url": "https://example.invalid/pkg.rpm.sha512"}

    monkeypatch.setattr(
        storydeps.urllib.request, "urlretrieve",
        lambda url, dest: Path(dest).write_bytes(payload),
    )

    class _Resp:
        def __init__(self, body): self._body = body
        def read(self): return self._body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(
        storydeps.urllib.request, "urlopen",
        lambda url, timeout=30: _Resp(f"{good}  pkg.rpm\n".encode()),
    )
    dest = storydeps._download_verified(asset, [asset, sha], tmp_path)
    assert dest.read_bytes() == payload

    monkeypatch.setattr(
        storydeps.urllib.request, "urlopen",
        lambda url, timeout=30: _Resp(b"0badc0ffee  pkg.rpm\n"),
    )
    with pytest.raises(ConfigError, match="sha512 mismatch"):
        storydeps._download_verified(asset, [asset, sha], tmp_path)


def test_story_flag_is_opt_in():
    from rodeo.commands import install_deps as mod

    params = {p.name: p for p in mod.install_deps_cmd.params}
    assert "story" in params
    assert params["story"].default is False
