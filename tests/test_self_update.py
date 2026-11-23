"""Tests for `rodeo self-update` — the anti-strand guarantees.

These lock in the two behaviours that stop a host being silently left on old
code: (1) if the working tree can't be aligned to the remote tip, the command
fails loudly instead of reporting success; (2) the reported version is read
fresh from disk, not from the running process's start-up value.
"""
from __future__ import annotations

import subprocess

import pytest
from click.testing import CliRunner

from rodeo.commands import self_update_cmd as su

from tests._util import plain_output


def _cp(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeGit:
    """Stand-in for _git that tracks a mutable HEAD and a fixed remote tip."""

    def __init__(self, head: str, target_sha: str, align: bool):
        self.head = head
        self.target_sha = target_sha
        self.align = align  # whether checkout/reset moves HEAD to the tip

    def __call__(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        a = list(args)
        if a[:2] == ["remote", "get-url"]:
            return _cp("git@github.com:avaleror/rodeo-cli.git\n")
        if a[:1] == ["symbolic-ref"]:
            return _cp("refs/remotes/origin/main\n")
        if a[:2] == ["rev-parse", "HEAD"]:
            return _cp(self.head + "\n")
        if a[:1] == ["rev-parse"]:  # rev-parse origin/main
            return _cp(self.target_sha + "\n")
        if a[:1] == ["fetch"]:
            return _cp()
        if a[:1] == ["status"]:
            return _cp()
        if a[:1] == ["checkout"] or a[:1] == ["reset"]:
            if self.align:
                self.head = self.target_sha
            return _cp()
        return _cp()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(su, "_REPO_ROOT", tmp_path)
    # pip install is a no-op success
    monkeypatch.setattr(su.subprocess, "run", lambda *a, **k: _cp())
    monkeypatch.setattr(su, "_pyproject_version", lambda: "0.11.2")
    return tmp_path


def test_strand_is_refused(repo, monkeypatch):
    """If the tree can't reach the remote tip, self-update must fail, not lie."""
    monkeypatch.setattr(su, "_git", FakeGit(head="oldsha", target_sha="newsha", align=False))
    monkeypatch.setattr(su, "_installed_version", lambda: "0.10.3")
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 1
    assert "did not reach" in result.output.lower() or "stale" in result.output.lower()


def test_successful_update_reports_fresh_version(repo, monkeypatch):
    """A real advance reports the newly installed version, not the start-up one."""
    monkeypatch.setattr(su, "_git", FakeGit(head="oldsha", target_sha="newsha", align=True))
    versions = iter(["0.10.3", "0.11.2"])  # before, after
    monkeypatch.setattr(su, "_installed_version", lambda: next(versions))
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 0
    out = plain_output(result.output)
    assert "0.11.2" in out
    assert "updated" in out.lower()


def test_already_up_to_date(repo, monkeypatch):
    monkeypatch.setattr(su, "_git", FakeGit(head="samesha", target_sha="samesha", align=True))
    monkeypatch.setattr(su, "_installed_version", lambda: "0.11.2")
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 0
    assert "up to date" in result.output.lower()


def test_fetch_forces_tags(repo, monkeypatch):
    """Regression: a rewritten upstream history reassigns every tag to a new
    SHA. A plain `--tags` fetch then rejects each pre-existing local tag as
    'would clobber existing tag' — git reports a non-zero exit even though the
    branch itself fetched fine, so self-update must force tags through rather
    than fail outright on any host with stale local tags."""
    calls: list[list[str]] = []

    class RecordingGit(FakeGit):
        def __call__(self, *args, check=True):
            a = list(args)
            if a[:1] == ["fetch"]:
                calls.append(a)
            return super().__call__(*args, check=check)

    monkeypatch.setattr(su, "_git", RecordingGit(head="oldsha", target_sha="newsha", align=True))
    monkeypatch.setattr(su, "_installed_version", lambda: "0.11.2")
    result = CliRunner().invoke(su.self_update_cmd, ["--branch", "main"])
    assert result.exit_code == 0
    assert len(calls) == 1
    assert "--force" in calls[0]
    assert "--tags" in calls[0]


def test_missing_remote_branch_fails(repo, monkeypatch):
    """A fork missing origin/main must fail loudly, not no-op to success.

    The branch is absent on the remote, so `git fetch` reports "couldn't find
    remote ref" — self-update must surface that, not report success.
    """
    class NoBranch(FakeGit):
        def __call__(self, *args, check=True):
            if list(args)[:1] == ["fetch"]:
                return _cp(returncode=128, stderr="fatal: couldn't find remote ref refs/heads/main")
            return super().__call__(*args, check=check)
    monkeypatch.setattr(su, "_git", NoBranch(head="oldsha", target_sha="newsha", align=False))
    monkeypatch.setattr(su, "_installed_version", lambda: "0.10.3")
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 1
    assert "does not exist" in result.output.lower()


class DirtyGit(FakeGit):
    """A clone with uncommitted changes and/or unpushed commits on main."""

    def __init__(self, *, dirty: str = "", unpushed: str = "", **kw):
        super().__init__(**kw)
        self.dirty, self.unpushed, self.reset_called = dirty, unpushed, False

    def __call__(self, *args, check=True):
        a = list(args)
        if a[:1] == ["status"]:
            return _cp(self.dirty)
        if a[:1] == ["rev-list"]:
            return _cp(self.unpushed)
        if a[:1] in (["checkout"], ["reset"]):
            self.reset_called = True
        return super().__call__(*args, check=check)


@pytest.mark.parametrize("state", [{"dirty": " M rodeo/cli.py\n"}, {"unpushed": "abc123\n"}])
def test_local_work_is_never_discarded_without_force(repo, monkeypatch, state):
    """rodeo often runs from a developer's own clone: self-update must refuse
    rather than reset away uncommitted changes or unpushed commits."""
    git = DirtyGit(head="oldsha", target_sha="newsha", align=True, **state)
    monkeypatch.setattr(su, "_git", git)
    monkeypatch.setattr(su, "_installed_version", lambda: "0.10.3")
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 1
    assert "--force" in plain_output(result.output)
    assert not git.reset_called


def test_force_discards_local_work(repo, monkeypatch):
    git = DirtyGit(head="oldsha", target_sha="newsha", align=True, dirty=" M x\n", unpushed="abc\n")
    monkeypatch.setattr(su, "_git", git)
    versions = iter(["0.10.3", "0.11.2"])
    monkeypatch.setattr(su, "_installed_version", lambda: next(versions))
    result = CliRunner().invoke(su.self_update_cmd, ["--force"])
    assert result.exit_code == 0
    assert git.reset_called and git.head == "newsha"


# ── restarting the Rodeo Builder service ──────────────────────────────────────
# A self-update that leaves rodeo-builder.service running keeps the old page in
# memory while serving the new app.js, and the page dies on markup it cannot find.


def _advance(monkeypatch):
    monkeypatch.setattr(su, "_git", FakeGit(head="oldsha", target_sha="newsha", align=True))
    versions = iter(["0.10.3", "0.11.2"])
    monkeypatch.setattr(su, "_installed_version", lambda: next(versions))


def test_update_restarts_the_running_builder(repo, monkeypatch):
    from rodeo import systemd

    _advance(monkeypatch)
    calls = []
    monkeypatch.setattr(systemd, "unit_is_active", lambda unit=systemd.BUILDER_UNIT: True)
    monkeypatch.setattr(systemd, "restart_unit", lambda unit=systemd.BUILDER_UNIT: (calls.append(unit), (True, ""))[1])
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 0 and calls == [systemd.BUILDER_UNIT]
    assert "restarted" in plain_output(result.output).lower()


def test_update_leaves_a_host_without_the_service_alone(repo, monkeypatch):
    from rodeo import systemd

    _advance(monkeypatch)
    monkeypatch.setattr(systemd, "unit_is_active", lambda unit=systemd.BUILDER_UNIT: False)
    monkeypatch.setattr(systemd, "restart_unit", lambda unit=systemd.BUILDER_UNIT: pytest.fail("restarted anyway"))
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 0 and "restart" not in plain_output(result.output).lower()


def test_no_restart_skips_it(repo, monkeypatch):
    from rodeo import systemd

    _advance(monkeypatch)
    monkeypatch.setattr(systemd, "unit_is_active", lambda unit=systemd.BUILDER_UNIT: pytest.fail("asked anyway"))
    result = CliRunner().invoke(su.self_update_cmd, ["--no-restart"])
    assert result.exit_code == 0


def test_a_failed_restart_is_reported_but_the_update_stands(repo, monkeypatch):
    from rodeo import systemd

    _advance(monkeypatch)
    monkeypatch.setattr(systemd, "unit_is_active", lambda unit=systemd.BUILDER_UNIT: True)
    monkeypatch.setattr(systemd, "restart_unit", lambda unit=systemd.BUILDER_UNIT: (False, "job failed"))
    result = CliRunner().invoke(su.self_update_cmd, [])
    out = plain_output(result.output)
    assert result.exit_code == 0 and "job failed" in out and "updated" in out.lower()


def test_up_to_date_does_not_restart(repo, monkeypatch):
    from rodeo import systemd

    monkeypatch.setattr(su, "_git", FakeGit(head="samesha", target_sha="samesha", align=True))
    monkeypatch.setattr(su, "_installed_version", lambda: "0.11.2")
    monkeypatch.setattr(systemd, "unit_is_active", lambda unit=systemd.BUILDER_UNIT: pytest.fail("asked anyway"))
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 0 and "up to date" in result.output.lower()


class StableGit(FakeGit):
    """A remote whose main carries STABLE=v0.32.2, with tags v0.32.2 and v0.33.0."""

    def __init__(self, *, stable: str = "v0.32.2\n", **kw):
        super().__init__(**kw)
        self.stable, self.calls = stable, []

    def __call__(self, *args, check=True):
        a = list(args)
        self.calls.append(a)
        if a[:1] == ["show"]:
            return _cp(self.stable) if self.stable else _cp(returncode=128, stderr="fatal: path 'STABLE' does not exist")
        if a[:1] == ["tag"]:
            return _cp("v0.33.0\nv0.32.2\nv0.32.1\n")
        if a[:3] == ["rev-parse", "-q", "--verify"]:
            return _cp(returncode=0 if a[3] in ("refs/tags/v0.32.2", "refs/tags/v0.33.0") else 1)
        if a[:1] == ["rev-parse"] and a[1].startswith("refs/tags/"):
            self.resolved = a[1]
            return _cp(self.target_sha + "\n")
        return super().__call__(*args, check=check)


def test_default_target_is_the_stable_release(repo, monkeypatch):
    """No --branch: land on the tag in STABLE, detached, not on main's tip."""
    git = StableGit(head="oldsha", target_sha="stablesha", align=True)
    monkeypatch.setattr(su, "_git", git)
    monkeypatch.setattr(su, "_installed_version", lambda: "0.32.2")
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 0, result.output
    assert git.resolved == "refs/tags/v0.32.2^{commit}"
    assert ["checkout", "--force", "--detach", "stablesha"] in git.calls
    assert "v0.32.2" in plain_output(result.output)
    fetch = next(c for c in git.calls if c[:1] == ["fetch"])
    assert "--tags" in fetch and "--force" in fetch


def test_missing_stable_falls_back_to_the_newest_tag(repo, monkeypatch):
    git = StableGit(stable="", head="oldsha", target_sha="newestsha", align=True)
    monkeypatch.setattr(su, "_git", git)
    monkeypatch.setattr(su, "_installed_version", lambda: "0.33.0")
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 0, result.output
    assert git.resolved == "refs/tags/v0.33.0^{commit}"
    assert "no valid STABLE" in plain_output(result.output)


def test_stable_naming_an_unknown_tag_is_not_trusted(repo, monkeypatch):
    git = StableGit(stable="v9.9.9\n", head="oldsha", target_sha="newestsha", align=True)
    monkeypatch.setattr(su, "_git", git)
    monkeypatch.setattr(su, "_installed_version", lambda: "0.33.0")
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 0, result.output
    assert git.resolved == "refs/tags/v0.33.0^{commit}"


def test_branch_option_still_follows_the_branch_tip(repo, monkeypatch):
    git = StableGit(head="oldsha", target_sha="mainsha", align=True)
    monkeypatch.setattr(su, "_git", git)
    monkeypatch.setattr(su, "_installed_version", lambda: "0.33.0")
    result = CliRunner().invoke(su.self_update_cmd, ["--branch", "main"])
    assert result.exit_code == 0, result.output
    assert not any(c[:1] == ["show"] for c in git.calls)
    assert ["checkout", "-B", "main", "mainsha"] in git.calls
