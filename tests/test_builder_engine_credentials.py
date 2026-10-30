"""Rodeo Builder: credentials of the lab-in-a-box engine's store (rodeo/builder/engine_credentials.py)."""
from __future__ import annotations

import base64
import json

import pytest
import yaml

from rodeo import labinabox_host
from rodeo.builder import engine_credentials as ec
from rodeo.builder import vault
from rodeo.builder.api import Api

PW = "engine passphrase"
FAKE_CRYPTO = '''
import base64


class DecryptionError(Exception):
    pass


def decrypt_cascade(passphrase, envelope):
    if passphrase != envelope["pw"]:
        raise DecryptionError("wrong passphrase")
    return base64.b64decode(envelope["ciphertext"])
'''


def _envelope(plain: str) -> dict:
    return {"encrypted": True, "kdf": "argon2id", "pw": PW, "ciphertext": base64.b64encode(plain.encode()).decode()}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    d = tmp_path / "credentials"
    d.mkdir()
    monkeypatch.setattr(labinabox_host, "CREDENTIALS_DIR", d)
    checkout = tmp_path / "checkout"
    (checkout / "libs").mkdir(parents=True)
    (checkout / "libs" / "crypto_store.py").write_text(FAKE_CRYPTO)
    (d / "vhm_aws-tmm.yaml").write_text(yaml.safe_dump(
        {"credential_kind": "vhm_aws", "unencrypted": True, "vhm_aws_access_key_id": "AK-PLAIN"}))
    (d / "aws-tmm.yaml").write_text(yaml.safe_dump(
        {"cloudtype": "aws", "AWS_REGION": "eu-west-2", "AWS_SECRET_ACCESS_KEY": _envelope("SK-FIELD")}))
    whole = _envelope(yaml.safe_dump({"appcollection_user": "u", "appcollection_password": "AP-WHOLE"}))
    (d / "appcollection-appco.yaml").write_text(yaml.safe_dump(
        {"credential_kind": "appcollection", "schema_version": "1", **whole}))
    (d / "aws-tmm.yaml.backup").write_text("x: 1\n")
    (d / "broken.yaml").write_text("key: [unclosed\n")
    (d / "list.yaml").write_text("- a\n")
    return checkout


def test_lists_names_and_engine_never_values(store):
    items = ec.list_items()
    assert [(i["id"], i["encrypted"]) for i in items] == [
        ("liab-appcollection-appco", True), ("liab-aws-tmm", True), ("liab-vhm_aws-tmm", False)]
    assert {i["engine"] for i in items} == {"lab-in-a-box"} and {i["type"] for i in items} == {"generic"}
    assert [i["app"] for i in items] == ["appcollection", "aws", "vhm_aws"]
    text = json.dumps(items)
    assert "AK-PLAIN" not in text and "SK-FIELD" not in text and "eu-west-2" not in text


def test_opens_each_shape(store):
    assert ec.open_item("liab-vhm_aws-tmm", "", store) == {"vhm_aws_access_key_id": "AK-PLAIN"}
    assert ec.open_item("liab-aws-tmm", PW, store) == {"aws_region": "eu-west-2", "aws_secret_access_key": "SK-FIELD"}
    assert ec.open_item("liab-appcollection-appco", PW, store) == {
        "appcollection_user": "u", "appcollection_password": "AP-WHOLE"}
    with pytest.raises(vault.WrongPassword):
        ec.open_item("liab-aws-tmm", "wrong", store)


@pytest.mark.parametrize("item_id,error", [
    ("liab-../etc/passwd", ValueError), ("liab-", ValueError), ("aws-tmm", ValueError), (None, ValueError),
    ("liab-nope", FileNotFoundError), ("liab-broken", ValueError),
])
def test_refuses_bad_ids_and_files(store, item_id, error):
    with pytest.raises(error):
        ec.open_item(item_id, PW, store)


def test_encrypted_needs_a_checkout(store, monkeypatch, tmp_path):
    monkeypatch.setattr(ec, "rodeo_dir", lambda: tmp_path / "none")
    monkeypatch.delenv(labinabox_host.LIAB_PATH_ENV, raising=False)
    assert ec.open_item("liab-vhm_aws-tmm", "", None) == {"vhm_aws_access_key_id": "AK-PLAIN"}
    with pytest.raises(ValueError, match="checkout"):
        ec.open_item("liab-aws-tmm", PW, None)


def test_missing_store_lists_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(labinabox_host, "CREDENTIALS_DIR", tmp_path / "missing")
    assert ec.list_items() == []


def test_builder_lists_and_opens_them(store):
    api = Api(store, can_save=True, can_deploy=True)
    vault.add("mine", "github", {"token": "t"}, "correct horse")
    _, body = api.dispatch("vault")
    assert [(i["name"], i["engine"]) for i in body["items"]] == [
        ("mine", "rodeo"), ("appcollection-appco", "lab-in-a-box"), ("aws-tmm", "lab-in-a-box"),
        ("vhm_aws-tmm", "lab-in-a-box")]
    post = lambda b: api.dispatch("vault-open", "POST", body=json.dumps(b).encode())  # noqa: E731
    assert post({"id": "liab-aws-tmm", "password": PW}) == (
        200, {"fields": {"aws_region": "eu-west-2", "aws_secret_access_key": "SK-FIELD"}})
    assert post({"id": "liab-aws-tmm", "password": "bad"}) == (403, {"error": "wrong password"})
    assert post({"id": "liab-x/../y", "password": PW})[0] == 400
