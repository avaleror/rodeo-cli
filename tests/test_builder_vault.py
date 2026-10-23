"""Rodeo Builder: the encrypted credential store (rodeo/builder/vault.py)."""
from __future__ import annotations

import json
import socket
import stat

import pytest

from rodeo import secretgen
from rodeo.builder import api as api_mod
from rodeo.builder import vault
from rodeo.builder.api import Api
from rodeo.paths import invoking_user

PW = "correct horse"


def _api(can_deploy: bool = True) -> Api:
    return Api(can_save=True, can_deploy=can_deploy)


def _post(api: Api, action: str, body: dict) -> tuple[int, dict]:
    return api.dispatch(action, "POST", body=json.dumps(body).encode("utf-8"))


def _file(item_id: str):
    return vault.vault_dir() / "{}.json".format(item_id)


def test_round_trip_and_used_updated():
    meta = vault.add("my scc", "scc", {"regcode": "ABC-123", "email": ""}, PW)
    assert set(meta) == {"id", "name", "type", "created", "used"}
    assert meta["used"] is None and vault.ID_RE.match(meta["id"])
    assert vault.open_item(meta["id"], PW) == {"regcode": "ABC-123"}
    [listed] = vault.list_items()
    assert listed["used"] is not None
    doc = json.loads(_file(meta["id"]).read_text())
    assert doc["kdf"]["name"] == "scrypt" and doc["kdf"]["n"] == 2 ** 15
    assert "ABC-123" not in _file(meta["id"]).read_text()


def test_wrong_password():
    meta = vault.add("aws", "aws", {"access_key_id": "AK", "secret_access_key": "SK"}, PW)
    with pytest.raises(vault.WrongPassword):
        vault.open_item(meta["id"], "not the password")
    assert vault.list_items()[0]["used"] is None


@pytest.mark.parametrize("edit", [
    lambda d: d.update(name="other"),
    lambda d: d.update(type="registry"),
    lambda d: d.update(ct=d["ct"][:-4] + ("AAAA" if d["ct"][-4:] != "AAAA" else "BBBB")),
])
def test_tampering_fails(edit):
    meta = vault.add("gh", "github", {"token": "ghp_x"}, PW)
    path = _file(meta["id"])
    doc = json.loads(path.read_text())
    edit(doc)
    path.write_text(json.dumps(doc))
    with pytest.raises(vault.WrongPassword):
        vault.open_item(meta["id"], PW)


def test_permissions():
    meta = vault.add("gh", "github", {"token": "t"}, PW)
    assert stat.S_IMODE(vault.vault_dir().stat().st_mode) == 0o700
    assert stat.S_IMODE(_file(meta["id"]).stat().st_mode) == 0o600
    assert [p.name for p in vault.vault_dir().iterdir()] == [meta["id"] + ".json"]


def test_list_has_metadata_only():
    vault.add("gh", "github", {"token": "secret-token"}, PW)
    status, body = _api().dispatch("vault")
    assert status == 200 and body["available"] is True and body["reason"] == ""
    assert {t["id"] for t in body["types"]} >= {"scc", "scc-mirror", "aws", "generic", "ssh-key"}
    [item] = body["items"]
    assert set(item) == {"id", "name", "type", "created", "used"}
    assert "secret-token" not in json.dumps(body)
    assert "ct" not in item


def test_unique_names_case_insensitive():
    vault.add("My Key", "github", {"token": "t"}, PW)
    with pytest.raises(ValueError, match="already exists"):
        vault.add("my key", "github", {"token": "t"}, PW)


@pytest.mark.parametrize("name,type_id,fields,password,match", [
    ("x", "github", {"token": "t"}, "short", "at least 8"),
    ("", "github", {"token": "t"}, PW, "name"),
    ("n" * 65, "github", {"token": "t"}, PW, "name"),
    ("x", "nope", {"token": "t"}, PW, "unknown credential type"),
    ("x", "github", {}, PW, "missing"),
    ("x", "github", {"token": ""}, PW, "missing"),
    ("x", "github", {"token": "t", "extra": "y"}, PW, "unknown fields"),
    ("x", "github", {"token": 5}, PW, "string"),
    ("x", "github", {"token": "a" * (64 * 1024 + 1)}, PW, "64 KiB"),
    ("x", "generic", {}, PW, "at least one"),
    ("x", "generic", {"Bad-Key": "v"}, PW, "field names"),
])
def test_validation(name, type_id, fields, password, match):
    with pytest.raises(ValueError, match=match):
        vault.add(name, type_id, fields, password)
    assert vault.list_items() == []


def test_generic_freeform():
    values = {"rancher_admin_password": "pw", "smlm_admin_password": "x"}
    meta = vault.add("mix", "generic", values, PW)
    assert vault.open_item(meta["id"], PW) == values
    generic = next(t for t in vault.types() if t["id"] == "generic")
    assert generic["freeform"] is True and generic["fields"] == []


@pytest.mark.parametrize("bad", ["../../etc/passwd", "0123456789abcdeF", "abc", "", None])
def test_id_traversal_rejected(bad):
    with pytest.raises(ValueError):
        vault.open_item(bad, PW)
    with pytest.raises(ValueError):
        vault.delete(bad)
    assert _post(_api(), "vault-open", {"id": bad, "password": PW})[0] == 400
    assert _post(_api(), "vault-delete", {"id": bad})[0] == 400


def test_api_add_open_delete():
    api = _api()
    status, body = _post(api, "vault-add", {"name": "m", "type": "scc-mirror",
                                            "fields": {"username": "u", "password": "p"}, "password": PW})
    assert status == 200
    item_id = body["item"]["id"]
    assert _post(api, "vault-open", {"id": item_id, "password": PW}) == (
        200, {"fields": {"username": "u", "password": "p"}})
    assert _post(api, "vault-open", {"id": item_id, "password": "nope-nope"}) == (
        403, {"error": "wrong password"})
    assert _post(api, "vault-add", {"name": "M", "type": "github", "fields": {"token": "t"},
                                    "password": PW})[0] == 400
    assert _post(api, "vault-delete", {"id": item_id}) == (200, {"ok": True})
    assert _post(api, "vault-delete", {"id": item_id})[0] == 404
    assert _post(api, "vault-open", {"id": item_id, "password": PW})[0] == 404


def test_rate_limit_with_fake_clock():
    now = [1000.0]
    api = _api()
    api.vault_limiter = vault.OpenLimiter(clock=lambda: now[0])
    item_id = vault.add("gh", "github", {"token": "t"}, PW)["id"]
    other = vault.add("gh2", "github", {"token": "u"}, PW)["id"]
    for _ in range(5):
        assert _post(api, "vault-open", {"id": item_id, "password": "wrong-pass"})[0] == 403
    assert _post(api, "vault-open", {"id": item_id, "password": PW}) == (
        429, {"error": "too many attempts, wait a minute"})
    assert _post(api, "vault-open", {"id": other, "password": PW})[0] == 200
    now[0] += 59
    assert _post(api, "vault-open", {"id": item_id, "password": PW})[0] == 429
    now[0] += 2
    assert _post(api, "vault-open", {"id": item_id, "password": PW})[0] == 200


def test_failures_outside_window_do_not_lock():
    now = [0.0]
    limiter = vault.OpenLimiter(clock=lambda: now[0])
    for _ in range(4):
        limiter.failed("a" * 16)
    now[0] += 61
    limiter.failed("a" * 16)
    limiter.check("a" * 16)


def _secrets_yaml(text: str) -> None:
    path = secretgen.secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_file_secrets_lists_keys_never_values():
    _secrets_yaml('# comment\nzeta_token: "VALUE-ZETA"\nalpha_password: "VALUE-ALPHA"\nport: 8443\n'
                  'flag: true\nratio: 1.5\nnested:\n  inner: VALUE-NESTED\nlisted: [VALUE-LISTED]\nempty:\n')
    status, body = _api().dispatch("vault")
    assert status == 200
    assert body["file_secrets"] == [{"key": k} for k in ("alpha_password", "flag", "port", "ratio", "zeta_token")]
    assert "VALUE-" not in json.dumps(body) and "8443" not in json.dumps(body)


@pytest.mark.parametrize("content", [None, "", "- a\n- b\n", "key: [unclosed\n", b"\xff\xfe: x\n"])
def test_file_secrets_empty_when_missing_or_invalid(content):
    if isinstance(content, bytes):
        secretgen.secrets_path().parent.mkdir(parents=True, exist_ok=True)
        secretgen.secrets_path().write_bytes(content)
    elif content is not None:
        _secrets_yaml(content)
    assert _api().dispatch("vault") == (200, {"available": True, "reason": "", "types": vault.types(),
                                              "items": [], "file_secrets": [],
                                              "user": "{}@{}".format(invoking_user(), socket.gethostname())})


def test_api_gated_on_can_deploy():
    vault.add("gh", "github", {"token": "t"}, PW)
    _secrets_yaml('my_key: "v"\n')
    api = _api(can_deploy=False)
    assert api.dispatch("vault") == (200, {"available": False,
                                           "reason": "stored credentials " + api_mod.LOOPBACK_ONLY,
                                           "types": [], "items": [], "file_secrets": []})
    for action in api_mod.VAULT_ACTIONS:
        assert _post(api, action, {"id": "0" * 16, "password": PW})[0] == 403
    assert Api().dispatch("vault-open", "POST", body=b"{}")[0] == 403


def test_missing_cryptography(monkeypatch):
    monkeypatch.setattr(api_mod, "vault", None)
    status, body = _api().dispatch("vault")
    assert status == 200 and body["available"] is False and "cryptography" in body["reason"]
    assert body["types"] == [] and body["items"] == []


def test_static_build_does_not_embed_vault():
    assert "vault" not in api_mod.PAGE_ACTIONS
    assert "vault" not in Api().static_data(require_labinabox=False)
