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
    assert "0.11.2" in result.output
    assert "updated" in result.output.lower()


def test_already_up_to_date(repo, monkeypatch):
    monkeypatch.setattr(su, "_git", FakeGit(head="samesha", target_sha="samesha", align=True))
    monkeypatch.setattr(su, "_installed_version", lambda: "0.11.2")
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 0
    assert "up to date" in result.output.lower()


def test_missing_remote_branch_fails(repo, monkeypatch):
    """A fork missing origin/main must fail loudly, not no-op to success."""
    class NoTip(FakeGit):
        def __call__(self, *args, check=True):
            if list(args)[:1] == ["rev-parse"] and "HEAD" not in args:
                return _cp(returncode=128, stderr="unknown revision")
            return super().__call__(*args, check=check)
    monkeypatch.setattr(su, "_git", NoTip(head="oldsha", target_sha="newsha", align=False))
    monkeypatch.setattr(su, "_installed_version", lambda: "0.10.3")
    result = CliRunner().invoke(su.self_update_cmd, [])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()
