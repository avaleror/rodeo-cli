"""Rodeo Builder: the encrypted credential store (rodeo/builder/vault.py)."""
from __future__ import annotations

import base64
import json
import stat

import pytest

from rodeo import labinabox_host, secretgen
from rodeo.builder import api as api_mod
from rodeo.builder import login, vault
from rodeo.builder.api import Api

PW = "correct horse"
AUTH = "Basic " + base64.b64encode(b"alice:alicepass").decode()


@pytest.fixture(autouse=True)
def _no_engine_store(tmp_path, monkeypatch):
    monkeypatch.setattr(labinabox_host, "CREDENTIALS_DIR", tmp_path / "engine-credentials")


@pytest.fixture()
def logins(monkeypatch):
    """login.verify accepting alice:alicepass only; the calls it got."""
    calls = []

    def verify(user, password):
        calls.append(user)
        return (user, password) == ("alice", "alicepass")

    monkeypatch.setattr(login, "verify", verify)
    return calls


def _api(can_deploy: bool = True) -> Api:
    return Api(can_save=True, can_deploy=can_deploy)


def _post(api: Api, action: str, body: dict, auth: str = "") -> tuple[int, dict]:
    return api.dispatch(action, "POST", body=json.dumps(body).encode("utf-8"), auth=auth)


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
    assert set(item) == {"id", "name", "type", "created", "used", "engine"}
    assert item["engine"] == "rodeo"
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
        vault.delete(bad, PW)
    assert _post(_api(), "vault-open", {"id": bad, "password": PW})[0] == 400


def test_delete_needs_the_password():
    item_id = vault.add("m", "scc-mirror", {"username": "u", "password": "p"}, PW)["id"]
    with pytest.raises(vault.WrongPassword):
        vault.delete(item_id, "not the password")
    assert _file(item_id).exists()
    vault.delete(item_id, PW)
    assert not _file(item_id).exists()
    with pytest.raises(FileNotFoundError):
        vault.delete(item_id, PW)


def test_api_add_open_delete_with_a_login(logins):
    api = _api()
    add = {"name": "m", "type": "scc-mirror", "fields": {"username": "u", "password": "p"}, "password": PW}
    assert _post(api, "vault-add", add) == (401, {"error": "log in first (Authorization: Basic)"})
    bad = "Basic " + base64.b64encode(b"alice:nope").decode()
    assert _post(api, "vault-add", add, bad) == (401, {"error": "wrong user or password"})
    assert vault.list_items() == []
    status, body = _post(api, "vault-add", add, AUTH)
    assert status == 200
    item_id = body["item"]["id"]
    assert _post(api, "vault-open", {"id": item_id, "password": PW}) == (
        200, {"fields": {"username": "u", "password": "p"}})
    assert _post(api, "vault-open", {"id": item_id, "password": "nope-nope"}) == (
        403, {"error": "wrong password"})
    assert _post(api, "vault-add", {**add, "name": "M"}, AUTH)[0] == 400
    assert _post(api, "vault-delete", {"id": item_id, "password": PW})[0] == 401
    assert _post(api, "vault-delete", {"id": item_id, "password": "nope-nope"}, AUTH) == (
        403, {"error": "wrong password"})
    assert _file(item_id).exists()
    assert _post(api, "vault-delete", {"id": item_id, "password": PW}, AUTH) == (200, {"ok": True})
    assert _post(api, "vault-delete", {"id": item_id, "password": PW}, AUTH)[0] == 404
    assert _post(api, "vault-open", {"id": item_id, "password": PW})[0] == 404
    assert _post(api, "vault-delete", {"id": "liab-aws-x", "password": PW}, AUTH) == (
        403, {"error": "credentials of another engine are read-only in rodeo"})
    assert logins.count("alice") == 2


def test_api_add_and_delete_work_on_an_exposed_builder(logins):
    api = _api(can_deploy=False)
    add = {"name": "m", "type": "github", "fields": {"token": "t"}, "password": PW}
    item_id = _post(api, "vault-add", add, AUTH)[1]["item"]["id"]
    assert _post(api, "vault-open", {"id": item_id, "password": PW})[0] == 403
    assert _post(api, "vault-delete", {"id": item_id, "password": PW}, AUTH) == (200, {"ok": True})


def test_exposed_builder_lists_and_opens_after_a_login(logins):
    api = Api(can_save=True, can_deploy=True, need_login=True)
    item_id = _post(api, "vault-add", {"name": "m", "type": "github", "fields": {"token": "t"}, "password": PW},
                    AUTH)[1]["item"]["id"]
    status, listed = api.dispatch("vault")
    assert status == 200 and listed["login"] and listed["items"] == [] and listed["file_secrets"] == []
    assert api.dispatch("login") == (401, {"error": "log in first (Authorization: Basic)"})
    assert _post(api, "vault-open", {"id": item_id, "password": PW})[0] == 401
    assert api.dispatch("login", auth=AUTH) == (200, {"user": "alice"})
    assert [i["id"] for i in api.dispatch("vault", auth=AUTH)[1]["items"]] == [item_id]
    assert _post(api, "vault-open", {"id": item_id, "password": PW}, AUTH) == (200, {"fields": {"token": "t"}})
    assert logins.count("alice") == 1


def test_a_login_is_checked_again_after_its_ttl(logins, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(api_mod.time, "monotonic", lambda: now[0])
    api = _api()
    assert api.dispatch("login", auth=AUTH)[0] == 200
    now[0] += api_mod.LOGIN_TTL - 1
    assert api.dispatch("login", auth=AUTH)[0] == 200
    now[0] += 2
    assert api.dispatch("login", auth=AUTH)[0] == 200
    assert logins.count("alice") == 2


def test_login_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(login, "login_file", lambda: tmp_path / "missing")
    status, body = _post(_api(), "vault-add", {"name": "m"}, AUTH)
    assert status == 501 and "rodeo logins add" in body["error"]


def test_failed_logins_wait_longer_from_the_fifth(logins):
    now = [1000.0]
    api = _api()
    api.login_limiter = vault.OpenLimiter(clock=lambda: now[0])
    bad = "Basic " + base64.b64encode(b"alice:nope").decode()
    add = {"name": "m", "type": "github", "fields": {"token": "t"}, "password": PW}
    for _ in range(5):
        assert _post(api, "vault-add", add, bad)[0] == 401
    assert _post(api, "vault-add", add, AUTH) == (429, {"error": "too many attempts, wait 60 seconds"})
    now[0] += 61
    assert _post(api, "vault-add", add, bad)[0] == 401
    assert _post(api, "vault-add", add, AUTH) == (429, {"error": "too many attempts, wait 120 seconds"})
    now[0] += 121
    assert _post(api, "vault-add", add, AUTH)[0] == 200


@pytest.mark.parametrize("header,expected", [
    ("Basic " + base64.b64encode(b"alice:pw:with:colons").decode(), ("alice", "pw:with:colons")),
    ("Basic " + base64.b64encode(b"-rf:pw").decode(), None),
    ("Basic " + base64.b64encode(b"nocolon").decode(), None),
    ("Basic !!!", None),
    ("Bearer x", None),
    ("", None),
])
def test_parse_basic(header, expected):
    assert login.parse_basic(header) == expected


def test_verify_checks_bcrypt_hashes_natively(tmp_path, monkeypatch):
    monkeypatch.setattr(login, "BCRYPT_ROUNDS", 4)
    users = tmp_path / "htpasswd"
    login.set_user("alice", "alicepass", users)
    assert login.verify("alice", "alicepass", users) is True
    assert login.verify("alice", "wrong", users) is False
    assert login.verify("nobody", "alicepass", users) is False
    assert login.verify("-x", "alicepass", users) is False
    assert login.verify("alice", "x" * 73, users) is False
    with open(users, "a") as f:
        f.write("# a comment\n\nbob:$apr1$saltsalt$dSONY5u48qptEo.I0ySla0\ncarol:{SHA}W6ph5Mm5Pz8GgiULbPgzG37mj9g=\n")
    assert login.verify("alice", "alicepass", users) is True
    assert login.verify("bob", "alicepass", users) is True and login.verify("bob", "wrong", users) is False
    with pytest.raises(login.LoginUnavailable, match="cannot check"):
        login.verify("carol", "whatever", users)
    assert login.list_users(users) == [("alice", "bcrypt"), ("bob", "apr1-md5"), ("carol", "other")]
    login.set_user("bob", "bobpass12", users)
    assert login.list_users(users)[1] == ("bob", "bcrypt") and login.verify("bob", "bobpass12", users) is True


def test_apr1_md5_matches_apaches_hashes():
    assert login.apr1_md5(b"alicepass", b"saltsalt") == "$apr1$saltsalt$dSONY5u48qptEo.I0ySla0"
    assert login.apr1_md5("pässword-longer-than-sixteen-bytes".encode(), b"Ab3") == "$apr1$Ab3$T/eLGHwexmQuwJkdn42W91"


def test_login_file_needs_a_user(tmp_path):
    with pytest.raises(login.LoginUnavailable, match="rodeo logins add"):
        login.verify("alice", "pw", tmp_path / "missing")
    empty = tmp_path / "empty"
    empty.write_text("# nobody\n")
    with pytest.raises(login.LoginUnavailable, match="no users yet"):
        login.verify("alice", "pw", empty)


def test_login_file_is_the_system_one_when_present_else_the_users(tmp_path, monkeypatch):
    system = tmp_path / "etc" / "htpasswd"
    monkeypatch.setattr(login, "SYSTEM_LOGIN_FILE", system)
    assert login.login_file() == tmp_path / ".rodeo" / "builder" / "htpasswd"
    system.parent.mkdir()
    system.write_text("")
    assert login.login_file() == system


def test_set_and_remove_user_keep_the_files_mode_and_write_through_links(tmp_path, monkeypatch):
    monkeypatch.setattr(login, "BCRYPT_ROUNDS", 4)
    with pytest.raises(ValueError, match="at least 8"):
        login.set_user("alice", "short", tmp_path / "f")
    with pytest.raises(ValueError, match="user name"):
        login.set_user("-alice", "alicepass1", tmp_path / "f")
    with pytest.raises(ValueError, match="72 bytes"):
        login.set_user("alice", "x" * 73, tmp_path / "f")
    assert not (tmp_path / "f").exists()
    fresh = login.set_user("alice", "alicepass", tmp_path / "new" / "htpasswd")
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600 and stat.S_IMODE(fresh.parent.stat().st_mode) == 0o700
    real = tmp_path / "real"
    real.write_text("")
    real.chmod(0o640)
    link = tmp_path / "link"
    link.symlink_to(real)
    assert login.set_user("alice", "alicepass", link) == real
    assert login.set_user("bob", "bobpass12", link) == real and link.is_symlink()
    assert stat.S_IMODE(real.stat().st_mode) == 0o640
    assert [u for u, _ in login.list_users(link)] == ["alice", "bob"]
    assert login.verify("bob", "bobpass12", link) is True
    login.set_user("bob", "newpass123", link)
    assert login.verify("bob", "bobpass12", link) is False and login.verify("bob", "newpass123", link) is True
    assert login.remove_user("bob", link) is True and login.remove_user("bob", link) is False
    assert login.list_users(link) == [("alice", "bcrypt")] and login.list_users(tmp_path / "none") == []


def test_rate_limit_with_fake_clock():
    now = [1000.0]
    api = _api()
    api.vault_limiter = vault.OpenLimiter(clock=lambda: now[0])
    item_id = vault.add("gh", "github", {"token": "t"}, PW)["id"]
    other = vault.add("gh2", "github", {"token": "u"}, PW)["id"]
    for _ in range(4):
        assert _post(api, "vault-open", {"id": item_id, "password": "wrong-pass"})[0] == 403
    assert _post(api, "vault-open", {"id": item_id, "password": "wrong-pass"})[0] == 403
    assert _post(api, "vault-open", {"id": item_id, "password": PW}) == (
        429, {"error": "too many attempts, wait 60 seconds"})
    assert _post(api, "vault-open", {"id": other, "password": PW})[0] == 200
    now[0] += 59
    assert _post(api, "vault-open", {"id": item_id, "password": PW})[0] == 429
    now[0] += 2
    assert _post(api, "vault-open", {"id": item_id, "password": PW})[0] == 200
    for _ in range(5):
        assert _post(api, "vault-open", {"id": item_id, "password": "wrong-pass"})[0] == 403
    assert _post(api, "vault-open", {"id": item_id, "password": PW})[1]["error"].endswith("wait 60 seconds")


def test_backoff_doubles_up_to_an_hour_and_resets():
    now = [0.0]
    limiter = vault.OpenLimiter(clock=lambda: now[0])
    waits = []
    for _ in range(12):
        limiter.failed("k")
        try:
            limiter.check("k")
            waits.append(0)
        except vault.TooManyAttempts as exc:
            waits.append(int(str(exc).split()[-2]))
        now[0] += waits[-1]
    assert waits == [0, 0, 0, 0, 60, 120, 240, 480, 960, 1920, 3600, 3600]
    now[0] += 3600 + 1
    limiter.failed("k")
    limiter.check("k")
    limiter.succeeded("k")
    for _ in range(4):
        limiter.failed("k")
    limiter.check("k")


def test_backoff_shared_through_a_file(tmp_path):
    path = tmp_path / "state" / "attempts.json"
    for _ in range(5):
        vault.OpenLimiter(path=path).failed("k")
    with pytest.raises(vault.TooManyAttempts):
        vault.OpenLimiter(path=path).check("k")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    vault.OpenLimiter(path=path).succeeded("k")
    vault.OpenLimiter(path=path).check("k")
    path.write_text("not json")
    vault.OpenLimiter(path=path).check("k")


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
                                              "items": [], "file_secrets": []})


def test_api_gated_on_can_deploy():
    vault.add("gh", "github", {"token": "t"}, PW)
    _secrets_yaml('my_key: "v"\n')
    api = _api(can_deploy=False)
    assert api.dispatch("vault") == (200, {"available": False,
                                           "reason": "storing credentials " + api_mod.LOOPBACK_ONLY,
                                           "types": [], "items": [], "file_secrets": []})
    assert _post(api, "vault-open", {"id": "0" * 16, "password": PW})[0] == 403
    for action in api_mod.VAULT_ACTIONS:
        assert Api().dispatch(action, "POST", body=b"{}", auth=AUTH)[0] == 403


def test_missing_cryptography(monkeypatch):
    monkeypatch.setattr(api_mod, "vault", None)
    status, body = _api().dispatch("vault")
    assert status == 200 and body["available"] is False and "cryptography" in body["reason"]
    assert body["types"] == [] and body["items"] == []


def test_static_build_does_not_embed_vault():
    assert "vault" not in api_mod.PAGE_ACTIONS
    assert "vault" not in Api().static_data(require_labinabox=False)
