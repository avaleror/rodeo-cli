"""rodeo-cli Settings page: the root helper (rodeo.builder.settings) and its CGI script."""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys

import pytest

from rodeo import labinabox_host, secretgen
from rodeo.builder import settings, vault

PW = "correct horse"
SETTINGS_DIR = settings.__file__.rsplit("/", 1)[0]


@pytest.fixture(autouse=True)
def engine_store(tmp_path, monkeypatch):
    d = tmp_path / "engine-credentials"
    d.mkdir()
    monkeypatch.setattr(labinabox_host, "CREDENTIALS_DIR", d)
    return d


@pytest.fixture()
def cgi():
    spec = importlib.util.spec_from_file_location("settings_cgi", SETTINGS_DIR + "/cgi-bin/settings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env(**kw):
    env = {"HTTPS": "on", "REMOTE_USER": "alice", "REQUEST_METHOD": "GET", "QUERY_STRING": "action=list"}
    env.update(kw)
    return env


def _post_env(action, body):
    return _env(REQUEST_METHOD="POST", QUERY_STRING="action=" + action, CONTENT_TYPE="application/json",
                CONTENT_LENGTH=str(len(body)))


def test_helper_list_add_delete():
    path = secretgen.secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('my_key: "VALUE"\n')
    status, body = settings.run("add", {"name": "gh", "type": "github", "fields": {"token": "T-SECRET"},
                                        "password": PW})
    assert status == 200
    item_id = body["item"]["id"]
    status, body = settings.run("list", {})
    assert status == 200 and [i["name"] for i in body["items"]] == ["gh"]
    assert body["items"][0]["engine"] == "rodeo" and body["file_secrets"] == [{"key": "my_key"}]
    assert {t["id"] for t in body["types"]} >= {"scc", "github", "generic"}
    assert "T-SECRET" not in json.dumps(body) and "VALUE" not in json.dumps(body)
    assert settings.run("delete", {"id": item_id, "password": "wrong-password"}) == (403, {"error": "wrong password"})
    assert vault.list_items()
    assert settings.run("delete", {"id": item_id, "password": PW}) == (200, {"ok": True})
    assert settings.run("delete", {"id": item_id, "password": PW}) == (404, {"error": "unknown credential"})
    assert vault.list_items() == []


def test_helper_lists_other_engines_read_only(engine_store):
    (engine_store / "vhm_aws-tmm.yaml").write_text("unencrypted: true\nvhm_aws_access_key_id: AK-PLAIN\n")
    vault.add("mine", "github", {"token": "t"}, PW)
    status, body = settings.run("list", {})
    assert [(i["name"], i["engine"]) for i in body["items"]] == [("mine", "rodeo"), ("vhm_aws-tmm", "lab-in-a-box")]
    assert "AK-PLAIN" not in json.dumps(body)
    assert settings.run("delete", {"id": "liab-vhm_aws-tmm", "password": PW}) == (
        403, {"error": "credentials of another engine are read-only in rodeo"})
    assert (engine_store / "vhm_aws-tmm.yaml").exists()


def test_helper_failed_deletes_wait_longer_from_the_fifth():
    item_id = vault.add("gh", "github", {"token": "t"}, PW)["id"]
    for _ in range(5):
        assert settings.run("delete", {"id": item_id, "password": "wrong-password"})[0] == 403
    status, body = settings.run("delete", {"id": item_id, "password": PW})
    assert status == 429 and body["error"].startswith("too many attempts, wait ")
    assert vault.list_items()
    assert (vault.vault_dir() / settings.ATTEMPTS).exists()
    assert [i["id"] for i in vault.list_items()] == [item_id]


@pytest.mark.parametrize("action,req,match", [
    ("add", {"name": "x", "type": "github", "fields": {"token": "t"}, "password": "short"}, "at least 8"),
    ("delete", {"id": "../x", "password": PW}, "bad credential id"),
    ("open", {}, "unknown action"),
])
def test_helper_refuses(action, req, match):
    status, body = settings.run(action, req)
    assert status == 400 and match in body["error"]


def test_helper_main_reads_stdin_and_uses_its_own_home(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SUDO_USER", "nobody")
    monkeypatch.setattr(settings.pwd, "getpwuid", lambda uid: type("P", (), {"pw_dir": str(tmp_path)})())
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(
        {"name": "gh", "type": "github", "fields": {"token": "t"}, "password": PW}).encode())))
    assert settings.main(["add"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == 200 and (tmp_path / ".rodeo" / "vault" / (out["body"]["item"]["id"] + ".json")).exists()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"[1]")))
    settings.main(["list"])
    assert json.loads(capsys.readouterr().out) == {"status": 400, "body": {"error": "send a JSON object"}}


@pytest.mark.parametrize("env,status,match", [
    ({"HTTPS": ""}, 403, "needs HTTPS"),
    ({"REMOTE_USER": ""}, 401, "log in to manage credentials"),
    ({"QUERY_STRING": "action=open"}, 400, "unknown action"),
    ({"REQUEST_METHOD": "POST"}, 405, "list needs GET"),
    ({"QUERY_STRING": "action=add"}, 405, "add needs POST"),
    ({"QUERY_STRING": "action=add", "REQUEST_METHOD": "POST", "CONTENT_TYPE": "text/plain"}, 415, "application/json"),
    ({"QUERY_STRING": "action=add", "REQUEST_METHOD": "POST", "CONTENT_TYPE": "application/json",
      "CONTENT_LENGTH": str(2 * 1024 * 1024)}, 413, "too large"),
])
def test_cgi_refuses_before_the_helper(cgi, monkeypatch, env, status, match):
    monkeypatch.setattr(cgi.subprocess, "run", lambda *a, **k: pytest.fail("helper called"))
    got_status, body = cgi.answer(_env(**env), io.BytesIO(b"{}"))
    assert got_status == status and match in body["error"]


def test_cgi_passes_the_request_to_the_helper(cgi, monkeypatch):
    calls = []

    def run(cmd, input, **kw):
        calls.append((cmd, input))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"status": 403, "body": {"error": "wrong password"}}).encode(), b"")

    monkeypatch.setattr(cgi.subprocess, "run", run)
    monkeypatch.setattr(cgi.os, "geteuid", lambda: 491)
    monkeypatch.setenv("RODEO_BUILDER_HELPER", "/opt/helper")
    body = b'{"id": "0123456789abcdef", "password": "x"}'
    assert cgi.answer(_post_env("delete", body), io.BytesIO(body)) == (403, {"error": "wrong password"})
    assert calls == [(["sudo", "-n", "/opt/helper", "delete"], body)]


def test_cgi_list_adds_the_logged_in_user(cgi, monkeypatch):
    monkeypatch.setattr(cgi.subprocess, "run", lambda cmd, input, **kw: subprocess.CompletedProcess(
        cmd, 0, json.dumps({"status": 200, "body": {"items": []}}).encode(), b""))
    monkeypatch.setattr(cgi.os, "geteuid", lambda: 0)
    assert cgi.answer(_env(), io.BytesIO(b"")) == (200, {"items": [], "user": "alice"})


@pytest.mark.parametrize("stdout", [b"", b"not json", b'{"status": 200}'])
def test_cgi_reports_a_broken_helper(cgi, monkeypatch, stdout):
    monkeypatch.setattr(cgi.subprocess, "run", lambda cmd, input, **kw: subprocess.CompletedProcess(cmd, 1, stdout, b"sudo: a password is required"))
    status, body = cgi.answer(_env(), io.BytesIO(b""))
    assert status == 500 and "rodeo-builder-helper failed" in body["error"]


def test_cgi_main_writes_a_cgi_response(cgi, monkeypatch, capsysbinary):
    monkeypatch.setattr(cgi.os, "environ", _env(REMOTE_USER=""))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    cgi.main()
    head, _, payload = capsysbinary.readouterr().out.partition(b"\r\n\r\n")
    assert head.split(b"\r\n")[0] == b"Status: 401 Unauthorized"
    assert b"Cache-Control: no-store" in head
    assert json.loads(payload) == {"error": "log in to manage credentials"}


def test_cgi_uses_the_standard_library_only():
    text = open(SETTINGS_DIR + "/cgi-bin/settings.py").read()
    assert text.startswith("#!/usr/bin/python3\n")
    imports = {line.split()[1].split(".")[0] for line in text.splitlines() if line.startswith(("import ", "from "))}
    assert imports <= {"json", "os", "subprocess", "sys"}
