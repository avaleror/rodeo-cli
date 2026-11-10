"""The rodeo catalog: published rodeos installed from their repositories (rodeo/catalog.py)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from rodeo import catalog
from rodeo.commands.new_cmd import new_cmd
from rodeo.commands.profiles_cmd import profiles_cmd
from rodeo.labseed import custom_profile_dir

PLAN = "name: ride\ntype: rancher\ndeployment_target: baremetal\ncredentials:\n  rancher_admin_password: ??rancher_admin_password\n"


def test_entries_keep_only_plain_files(tmp_path):
    (tmp_path / "ride.yaml").write_text("name: ride\nrepo: https://github.com/rider/ride\nref: main\nengine: rancher\n")
    (tmp_path / "other.yaml").write_text("name: not-other\nrepo: https://github.com/a/b\n")
    (tmp_path / "bad.yaml").write_text("name: bad\nrepo: http://github.com/a/b\n")
    (tmp_path / "opt.yaml").write_text("name: opt\nrepo: https://git.example.com/team/opt.git\n")
    (tmp_path / "junk.yaml").write_text(": : :\n")
    assert sorted(catalog.entries(tmp_path)) == ["opt", "ride"]
    assert catalog.find("opt", tmp_path)["ref"] == ""
    assert catalog.find("missing", tmp_path) is None


def test_bundled_catalog_entries_are_valid():
    for path in catalog.CATALOG_DIR.glob("*.yaml"):
        assert path.stem in catalog.entries(), path


@pytest.mark.parametrize("spec,want", [
    ("https://github.com/rider/ride", ("https://github.com/rider/ride", "")),
    ("https://github.com/rider/ride@v1.2", ("https://github.com/rider/ride", "v1.2")),
    ("https://github.com/rider/ride.git@feature/x", ("https://github.com/rider/ride.git", "feature/x")),
])
def test_parse_spec(spec, want):
    assert catalog.parse_spec(spec) == want


@pytest.mark.parametrize("spec", ["http://github.com/a/b", "https://user:pw@github.com/a/b", "https://github.com/a",
                                  "https://github.com/a/b@-x", "https://github.com/a/b/tree/main"])
def test_parse_spec_refuses(spec):
    with pytest.raises(ValueError):
        catalog.parse_spec(spec)


def _published_repo(root: Path) -> Path:
    repo = root / "ride"
    (repo / "story").mkdir(parents=True)
    (repo / "checks").mkdir()
    (repo / "rodeo-plan.yaml").write_text(PLAN)
    (repo / "story" / "01-hello.md").write_text("# Hello\n")
    (repo / "checks" / "check-hello.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "checks" / "check-hello.sh").chmod(0o755)
    (repo / "notes.txt").write_text("not a rodeo file\n")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "publish"], check=True)
    return repo


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_install_fetches_the_repo_into_a_custom_profile(tmp_path):
    repo = _published_repo(tmp_path)
    dest = catalog.install("ride", str(repo), "main")
    assert dest == custom_profile_dir("ride")
    assert (dest / "story" / "01-hello.md").read_text() == "# Hello\n"
    assert (dest / "checks" / "check-hello.sh").stat().st_mode & 0o111
    assert not (dest / "notes.txt").exists() and not (dest / ".git").exists()
    lock = yaml.safe_load((dest / catalog.LOCK).read_text())
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert lock == {"repo": str(repo), "ref": "main", "commit": head}
    with pytest.raises(FileExistsError):
        catalog.install("ride", str(repo))
    with pytest.raises(RuntimeError, match="fetching"):
        catalog.install("other", str(tmp_path / "nope"))


def test_new_from_a_repository_url(monkeypatch, tmp_path):
    seen = []

    def fake(name, repo, ref="", force=False):
        seen.append((name, repo, ref, force))
        return tmp_path / name

    monkeypatch.setattr(catalog, "install", fake)
    r = CliRunner().invoke(new_cmd, ["ride", "--from", "https://github.com/rider/ride@v1", "--force"])
    assert r.exit_code == 0, r.output
    assert seen == [("ride", "https://github.com/rider/ride", "v1", True)]
    r = CliRunner().invoke(new_cmd, ["ride", "--from", "https://github.com/rider"])
    assert r.exit_code == 1 and "expected https://" in r.output


def test_profiles_lists_catalog_rodeos(monkeypatch):
    monkeypatch.setattr(catalog, "entries", lambda: {"ride": {"name": "ride", "repo": "https://github.com/rider/ride", "ref": "v1"}})
    r = CliRunner().invoke(profiles_cmd, [])
    assert r.exit_code == 0 and "catalog" in r.output and "rider/ride@v1" in r.output


def test_up_installs_a_catalog_rodeo_it_does_not_have(monkeypatch):
    from rodeo.commands import up_cmd

    installed = []
    monkeypatch.setattr(catalog, "find", lambda name: {"repo": "https://github.com/rider/ride", "ref": ""} if name == "ride" else None)
    monkeypatch.setattr(catalog, "install", lambda name, repo, ref: installed.append((name, repo, ref)) or Path("/p") / name)
    up_cmd._install_from_catalog("ride")
    up_cmd._install_from_catalog("unknown")
    assert installed == [("ride", "https://github.com/rider/ride", "")]

    def broken(name, repo, ref):
        raise RuntimeError("fetching https://github.com/rider/ride: not found")
    monkeypatch.setattr(catalog, "install", broken)
    with pytest.raises(SystemExit):
        up_cmd._install_from_catalog("ride")
