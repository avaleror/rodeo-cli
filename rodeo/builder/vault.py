"""Encrypted credential store for the live Rodeo Builder.

One file per credential, ``~/.rodeo/vault/<id>.json`` (directory 0700, files
0600, written atomically). Each credential is encrypted with its own password:
AES-256-GCM with a key derived by scrypt from that password and a random salt.
The plaintext is the JSON of the credential's field values; the associated data
is the canonical JSON of ``v``, ``id``, ``name``, ``type`` and ``created``, so a
changed name or type fails decryption. ``used`` (UTC time of the last
successful :func:`open_item`) is stored outside the associated data.

Passwords are never stored, cached or logged: every :func:`open_item` call
needs the password again. :data:`TYPES` lists the credential types the page
offers and, per type, which plan secret keys (``??key``) each field fills.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..paths import invoking_home

VERSION = 1
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAX_N = 2 ** 20
MIN_PASSWORD = 8
MAX_NAME = 64
MAX_VALUE = 64 * 1024
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW = 60.0
LOCKOUT = 60.0
ID_RE = re.compile(r"^[0-9a-f]{16}$")
FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _field(name: str, label: str, secret: bool = False, optional: bool = False,
           multiline: bool = False) -> dict[str, Any]:
    return {"name": name, "label": label, "secret": secret, "optional": optional, "multiline": multiline}


TYPES: tuple[dict[str, Any], ...] = (
    {"id": "scc", "label": "SCC registration code",
     "fields": [_field("regcode", "Registration code", secret=True),
                _field("email", "E-mail", optional=True)],
     "keys": {"scc_regcode": "regcode"}},
    {"id": "scc-mirror", "label": "SCC mirroring credentials",
     "fields": [_field("username", "Username"), _field("password", "Password", secret=True)],
     "keys": {"scc_mirror_user": "username", "scc_mirror_password": "password"}},
    {"id": "registry", "label": "SUSE registry",
     "fields": [_field("username", "Username"), _field("password", "Password", secret=True)],
     "keys": {}},
    {"id": "appco", "label": "Application Collection",
     "fields": [_field("username", "Username"), _field("token", "Token", secret=True)],
     "keys": {}},
    {"id": "aws", "label": "AWS",
     "fields": [_field("access_key_id", "Access key ID"),
                _field("secret_access_key", "Secret access key", secret=True),
                _field("session_token", "Session token", secret=True, optional=True),
                _field("region", "Region", optional=True)],
     "keys": {"aws_access_key_id": "access_key_id", "aws_secret_access_key": "secret_access_key"}},
    {"id": "ssh-key", "label": "SSH private key",
     "fields": [_field("private_key", "Private key", secret=True, multiline=True),
                _field("passphrase", "Passphrase", secret=True, optional=True)],
     "keys": {}},
    {"id": "github", "label": "GitHub token",
     "fields": [_field("token", "Token", secret=True)],
     "keys": {}},
    {"id": "generic", "label": "Generic", "fields": [], "freeform": True, "keys": {}},
)
_TYPES = {t["id"]: t for t in TYPES}

_lock = threading.Lock()


class WrongPassword(Exception):
    """The password does not decrypt the credential (or its file was altered)."""


class TooManyAttempts(Exception):
    """Too many failed opens of one credential; wait before trying again."""


def vault_dir() -> Path:
    return invoking_home() / ".rodeo" / "vault"


def types() -> list[dict[str, Any]]:
    """The credential types, as served to the page (copies)."""
    return json.loads(json.dumps(TYPES))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _path(item_id: str) -> Path:
    if not isinstance(item_id, str) or not ID_RE.match(item_id):
        raise ValueError("bad credential id")
    return vault_dir() / "{}.json".format(item_id)


def _aad(doc: dict[str, Any]) -> bytes:
    meta = {k: doc[k] for k in ("v", "id", "name", "type", "created")}
    return json.dumps(meta, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _key(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          maxmem=256 * r * n + 1024 * 1024, dklen=32)


def _meta(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: doc.get(k) for k in ("id", "name", "type", "created", "used")}


def _write(path: Path, doc: dict[str, Any]) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(path: Path) -> dict[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("v") != VERSION:
        raise ValueError("unsupported credential file {}".format(path.name))
    return doc


def _docs() -> list[dict[str, Any]]:
    out = []
    directory = vault_dir()
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        if not ID_RE.match(path.stem):
            continue
        try:
            out.append(_read(path))
        except (OSError, ValueError):
            continue
    return out


def list_items() -> list[dict[str, Any]]:
    """Metadata of every credential (``id``, ``name``, ``type``, ``created``, ``used``), by name."""
    return sorted((_meta(d) for d in _docs()), key=lambda m: str(m["name"]).lower())


def _check_fields(type_id: str, fields: Any) -> dict[str, str]:
    kind = _TYPES.get(type_id) if isinstance(type_id, str) else None
    if kind is None:
        raise ValueError("unknown credential type: {}".format(type_id))
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    for key, value in fields.items():
        if not isinstance(value, str):
            raise ValueError("field {} must be a string".format(key))
        if len(value.encode("utf-8")) > MAX_VALUE:
            raise ValueError("field {} is larger than 64 KiB".format(key))
    if kind.get("freeform"):
        clean = {k: v for k, v in fields.items() if v != ""}
        bad = [k for k in fields if not isinstance(k, str) or not FIELD_RE.match(k)]
        if bad:
            raise ValueError("field names must match {}: {}".format(FIELD_RE.pattern, ", ".join(map(str, bad))))
        if not clean:
            raise ValueError("a generic credential needs at least one field")
        return clean
    known = {f["name"]: f for f in kind["fields"]}
    unknown = sorted(k for k in fields if k not in known)
    if unknown:
        raise ValueError("unknown fields for {}: {}".format(type_id, ", ".join(map(str, unknown))))
    missing = [n for n, f in known.items() if not f["optional"] and not fields.get(n)]
    if missing:
        raise ValueError("missing fields: {}".format(", ".join(missing)))
    return {k: v for k, v in fields.items() if v != ""}


def add(name: str, type_id: str, fields: dict[str, str], password: str) -> dict[str, Any]:
    """Encrypt *fields* with *password* into a new credential; returns its metadata.
    Raises ValueError on a bad name, type, field or password."""
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME:
        raise ValueError("name must be 1 to {} characters".format(MAX_NAME))
    name = name.strip()
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise ValueError("password must be at least {} characters".format(MIN_PASSWORD))
    values = _check_fields(type_id, fields)
    salt, nonce = os.urandom(16), os.urandom(12)
    with _lock:
        if any(str(d.get("name", "")).lower() == name.lower() for d in _docs()):
            raise ValueError("a credential named {} already exists".format(name))
        item_id = secrets.token_hex(8)
        while _path(item_id).exists():
            item_id = secrets.token_hex(8)
        doc: dict[str, Any] = {"v": VERSION, "id": item_id, "name": name, "type": type_id,
                               "created": _now(), "used": None,
                               "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                                       "salt": _b64(salt)},
                               "nonce": _b64(nonce)}
        key = _key(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
        plain = json.dumps(values, ensure_ascii=False).encode("utf-8")
        doc["ct"] = _b64(AESGCM(key).encrypt(nonce, plain, _aad(doc)))
        _write(_path(item_id), doc)
    return _meta(doc)


def delete(item_id: str) -> None:
    """Remove a credential. Raises ValueError on a bad id, FileNotFoundError when unknown."""
    path = _path(item_id)
    with _lock:
        path.unlink()


def open_item(item_id: str, password: str) -> dict[str, str]:
    """The credential's field values. Raises ValueError on a bad id or file,
    FileNotFoundError when unknown and :class:`WrongPassword` when *password*
    does not decrypt it. A successful open sets ``used``."""
    path = _path(item_id)
    doc = _read(path)
    try:
        kdf = doc["kdf"]
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        if kdf.get("name") != "scrypt" or not (2 <= n <= SCRYPT_MAX_N) or not (1 <= r <= 32) or not (1 <= p <= 16):
            raise ValueError("unsupported key derivation")
        salt = base64.b64decode(kdf["salt"], validate=True)
        nonce = base64.b64decode(doc["nonce"], validate=True)
        ct = base64.b64decode(doc["ct"], validate=True)
        aad = _aad(doc)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("damaged credential file {}: {}".format(path.name, exc)) from None
    key = _key(password if isinstance(password, str) else "", salt, n, r, p)
    try:
        plain = AESGCM(key).decrypt(nonce, ct, aad)
    except (InvalidTag, ValueError):
        raise WrongPassword("wrong password") from None
    values = json.loads(plain.decode("utf-8"))
    with _lock:
        if path.exists():
            doc["used"] = _now()
            _write(path, doc)
    return {str(k): str(v) for k, v in values.items()}


class OpenLimiter:
    """Per-credential failed-open limit: after :data:`MAX_ATTEMPTS` failures within
    :data:`ATTEMPT_WINDOW` seconds, :meth:`check` refuses that id for :data:`LOCKOUT` seconds."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._fails: dict[str, list[float]] = {}
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, item_id: str) -> None:
        """Raises :class:`TooManyAttempts` while *item_id* is locked out."""
        with self._lock:
            until = self._until.get(item_id)
            if until is None:
                return
            if self.clock() < until:
                raise TooManyAttempts("too many attempts, wait a minute")
            del self._until[item_id]
            self._fails.pop(item_id, None)

    def failed(self, item_id: str) -> None:
        with self._lock:
            now = self.clock()
            fails = [t for t in self._fails.get(item_id, []) if now - t < ATTEMPT_WINDOW]
            fails.append(now)
            self._fails[item_id] = fails
            if len(fails) >= MAX_ATTEMPTS:
                self._until[item_id] = now + LOCKOUT

    def succeeded(self, item_id: str) -> None:
        with self._lock:
            self._fails.pop(item_id, None)
            self._until.pop(item_id, None)
