"""Rodeo Builder: publishing a rodeo to GitHub with the host's login (rodeo/builder/publish.py)."""
from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from rodeo.builder import publish
from rodeo.builder.api import Api

TOKEN = "gho_secret123"
FILES = [{"path": "rodeo-plan.yaml", "content": "name: ride\n"},
         {"path": "story/01-hello.md", "content": "# Hello\n"},
         {"path": "checks/check-hello.sh", "content": "#!/bin/sh\nexit 0\n", "executable": True}]


@pytest.fixture(autouse=True)
def no_ambient_login(monkeypatch):
    for var in ("GH_TOKEN", "GITHUB_TOKEN", "RODEO_CATALOG_REPO"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(publish.shutil, "which", lambda name: None)


def _done(cmd, rc=0, out=""):
    return subprocess.CompletedProcess(cmd, rc, out, "")


def test_credentials_prefer_env_then_gh_then_git(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[:3] == ["gh", "auth", "token"]:
            return _done(cmd, 0, "gho_from_gh\n")
        assert kw["env"]["GIT_TERMINAL_PROMPT"] == "0" and "host=github.com" in kw["input"]
        return _done(cmd, 0, "protocol=https\nhost=github.com\nusername=me\npassword=ghp_from_git\n")

    assert publish.credentials(run) == ("ghp_from_git", "git")
    monkeypatch.setattr(publish.shutil, "which", lambda name: "/usr/bin/gh")
    assert publish.credentials(run) == ("gho_from_gh", "gh")
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    assert publish.credentials(run) == ("env-token", "GITHUB_TOKEN")
    monkeypatch.setenv("GH_TOKEN", "gh-env-token")
    assert publish.credentials(run) == ("gh-env-token", "GH_TOKEN")


def test_credentials_none_when_git_has_no_login():
    assert publish.credentials(lambda cmd, **kw: _done(cmd, 128)) == ("", "")
    assert publish.credentials(lambda cmd, **kw: _done(cmd, 0, "username=\npassword=\n")) == ("", "")


def test_status_says_who_is_logged_in(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    seen = []

    def http(method, url, token, body):
        seen.append((method, url, token))
        return 200, {"login": "rider"}

    out = publish.status(http)
    assert (out["logged_in"], out["login"], out["source"], out["catalog"]) == (True, "rider", "GH_TOKEN", "avaleror/rodeo-cli")
    assert seen == [("GET", "https://api.github.com/user", TOKEN)]
    refused = publish.status(lambda *a: (401, {"message": "Bad credentials"}))
    assert not refused["logged_in"] and "Bad credentials" in refused["error"]
    monkeypatch.delenv("GH_TOKEN")
    assert publish.status(http, run=lambda cmd, **kw: _done(cmd, 1))["logged_in"] is False


class FakeGitHub:
    def __init__(self, login="rider", exists=False):
        self.login, self.exists, self.calls, self.forked = login, exists, [], False

    def __call__(self, method, url, token, body):
        assert token == TOKEN
        path = url.replace("https://api.github.com", "")
        self.calls.append((method, path, body))
        if path == "/user":
            return 200, {"login": self.login, "id": 42, "name": "Rider One"}
        if path == "/user/repos":
            return (422, {"message": "Repository creation failed.", "errors": [{"message": "name already exists on this account"}]}) \
                if self.exists else (201, {"full_name": self.login + "/ride"})
        if path == "/repos/avaleror/rodeo-cli":
            return 200, {"default_branch": "main"}
        if path == "/repos/avaleror/rodeo-cli/git/ref/heads/main":
            return 200, {"object": {"sha": "abc123"}}
        if path == "/repos/avaleror/rodeo-cli/forks":
            self.forked = True
            return 202, {"full_name": self.login + "/rodeo-cli"}
        if path == "/repos/{}/rodeo-cli".format(self.login):
            return 200, {}
        if path.endswith("/git/refs"):
            return 201, {}
        if "/contents/" in path and method == "GET":
            return 404, {"message": "Not Found"}
        if "/contents/" in path and method == "PUT":
            return 201, {}
        if path == "/repos/avaleror/rodeo-cli/pulls":
            return 201, {"html_url": "https://github.com/avaleror/rodeo-cli/pull/200"}
        return 404, {"message": "unexpected " + path}


def _req(**kw):
    return {"name": "ride", "title": "Ride the range", "engine": "rancher", "languages": ["en", "es", "BAD"],
            "files": FILES, **kw}


def test_publish_creates_the_repo_pushes_and_opens_the_catalog_pr(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    gh, pushed = FakeGitHub(), []
    monkeypatch.setattr(publish, "_push", lambda run, files, remote, author, token, message, update:
                        pushed.append((remote, author, [f.path for f in files], message, update)))
    out = publish.publish(_req(), http=gh, sleep=lambda s: None)
    assert out == {"repo": "https://github.com/rider/ride", "login": "rider", "catalog_error": "",
                   "pull_request": "https://github.com/avaleror/rodeo-cli/pull/200"}
    assert pushed == [("https://github.com/rider/ride.git", ("Rider One", "42+rider@users.noreply.github.com"),
                       ["rodeo-plan.yaml", "story/01-hello.md", "checks/check-hello.sh"], "Publish the rodeo ride", False)]
    create = next(c for c in gh.calls if c[1] == "/user/repos")[2]
    assert create["private"] is False and create["name"] == "ride"
    put = next(c for c in gh.calls if c[0] == "PUT")
    assert put[1].startswith("/repos/rider/rodeo-cli/contents/rodeo/data/catalog.d/ride.yaml")
    entry = yaml.safe_load(base64.b64decode(put[2]["content"]))
    assert entry == {"name": "ride", "title": "Ride the range", "repo": "https://github.com/rider/ride", "ref": "main",
                     "engine": "rancher", "languages": ["en", "es"]}
    pr = next(c for c in gh.calls if c[1].endswith("/pulls"))[2]
    assert pr["head"].startswith("rider:catalog-ride-") and pr["base"] == "main" and gh.forked


def test_publish_branches_in_the_catalog_repo_when_the_user_owns_it(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setattr(publish, "_push", lambda *a: None)
    gh = FakeGitHub(login="avaleror")
    publish.publish(_req(), http=gh, sleep=lambda s: None)
    pr = next(c for c in gh.calls if c[1].endswith("/pulls"))[2]
    assert not gh.forked and pr["head"].startswith("catalog-ride-")


def test_publish_update_pushes_without_a_pull_request(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    pushed = []
    monkeypatch.setattr(publish, "_push", lambda run, files, remote, author, token, message, update: pushed.append(update))
    gh = FakeGitHub(exists=True)
    with pytest.raises(FileExistsError, match="github.com/rider/ride already exists"):
        publish.publish(_req(), http=gh)
    out = publish.publish(_req(update=True), http=gh)
    assert out["pull_request"] == "" and pushed == [True]
    assert [c[1] for c in gh.calls].count("/user/repos") == 1


def test_publish_refuses_bad_requests_and_no_login(monkeypatch):
    with pytest.raises(ValueError):
        publish.publish(_req(name="Bad Name"))
    with pytest.raises(ValueError, match="rodeo-plan.yaml"):
        publish.publish(_req(files=[{"path": "story/a.md", "content": "x"}]))
    with pytest.raises(ValueError, match="not a file"):
        publish.publish(_req(files=FILES + [{"path": "../etc/passwd", "content": "x"}]))
    with pytest.raises(PermissionError, match="log in"):
        publish.publish(_req(), run=lambda cmd, **kw: _done(cmd, 1))


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_push_commits_the_files_and_hides_the_token(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    files = [publish.ProfileFile(f["path"], f["content"].encode(), bool(f.get("executable"))) for f in FILES]
    publish._push(subprocess.run, files, str(bare), ("Rider", "1+rider@users.noreply.github.com"), TOKEN, "Publish", False)
    tree = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "main"], capture_output=True, text=True, check=True).stdout
    assert "100755 blob" in tree.split("checks/check-hello.sh")[0].splitlines()[-1]
    assert "story/01-hello.md" in tree
    log = subprocess.run(["git", "-C", str(bare), "log", "--format=%an <%ae> %s", "main"], capture_output=True, text=True).stdout
    assert log.strip() == "Rider <1+rider@users.noreply.github.com> Publish"
    publish._push(subprocess.run, files[:1], str(bare), ("Rider", "r@x"), TOKEN, "Update", True)
    tree = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "main"], capture_output=True, text=True).stdout
    assert tree.split() == ["rodeo-plan.yaml"]
    with pytest.raises(publish.PublishError) as err:
        publish._push(subprocess.run, files, "https://" + TOKEN + "@127.0.0.1:9/x.git", ("R", "r@x"), TOKEN, "m", True)
    assert TOKEN not in str(err.value)


class FakeProc:
    def __init__(self, lines, rc=None):
        self.stdout, self.returncode, self._rc = io.StringIO("".join(lines)), None, rc

    def poll(self):
        self.returncode = self._rc
        return self._rc


def test_start_login_shows_gh_device_code(monkeypatch):
    monkeypatch.setattr(publish.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setitem(publish._login, "proc", None)
    seen = {}

    def popen(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"])
        return FakeProc(["! First copy your one-time code: AB12-CD34\n", "Open this URL to continue: https://github.com/login/device\n"])

    state = publish.start_login(popen, wait=2)
    assert state == {"pending": True, "code": "AB12-CD34", "url": "https://github.com/login/device", "error": ""}
    assert seen["cmd"][:3] == ["gh", "auth", "login"] and "--web" in seen["cmd"] and seen["env"]["GH_BROWSER"] == "true"
    publish._login["proc"]._rc = 1
    assert publish.login_state()["error"] == "gh auth login exited with 1"
    monkeypatch.setattr(publish.shutil, "which", lambda name: None)
    with pytest.raises(publish.PublishError, match="not installed"):
        publish.start_login(popen)


def test_api_publishes_only_on_a_loopback_live_server(monkeypatch):
    body = json.dumps(_req()).encode()
    assert Api().dispatch("publish", "POST", body=body)[0] == 403
    status, answer = Api(can_save=True).dispatch("publish", "POST", body=body)
    assert status == 403 and "listens on this machine only" in answer["error"]
    assert Api(can_save=True).dispatch("github") == (200, {"available": False})
    live = Api(can_save=True, can_publish=True)
    monkeypatch.setattr(publish, "status", lambda: {"logged_in": False})
    assert live.dispatch("github") == (200, {"logged_in": False, "available": True})
    monkeypatch.setattr(publish, "publish", lambda req: {"repo": "https://github.com/rider/" + req["name"]})
    assert live.dispatch("publish", "POST", body=body) == (200, {"repo": "https://github.com/rider/ride"})

    def no_login(req):
        raise PermissionError("log in to GitHub first")
    monkeypatch.setattr(publish, "publish", no_login)
    assert live.dispatch("publish", "POST", body=body) == (401, {"error": "log in to GitHub first", "login": True})


def test_builder_command_publishes_only_on_loopback():
    src = Path(publish.__file__).parents[1] / "commands" / "builder_cmd.py"
    assert "can_publish=_is_loopback(bind)" in src.read_text()


def test_a_refused_catalog_pr_still_publishes_and_says_why(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setattr(publish, "_push", lambda *a: None)
    gh = FakeGitHub()
    real = gh.__call__

    def http(method, url, token, body):
        if url.endswith("/pulls"):
            return 403, {"message": "Resource not accessible by personal access token"}
        return real(method, url, token, body)

    out = publish.publish(_req(), http=http, sleep=lambda s: None)
    assert out["repo"] == "https://github.com/rider/ride" and out["pull_request"] == ""
    assert "opening the catalog pull request (GitHub 403)" in out["catalog_error"] and "gh auth login" in out["catalog_error"]
