"""Logins for the builder: credential writes (POST ``vault-add``, ``vault-delete``) everywhere,
deploying and stored credentials on an exposed builder.

HTTP Basic (``Authorization: Basic <user:password>``), checked in Python against an
htpasswd-format file (``user:hash`` lines). Hashes are bcrypt (``$2a$``, ``$2b$`` or ``$2y$``:
what ``rodeo logins add`` and ``htpasswd -B`` write) or, for users made with ``htpasswd -m``,
APR1-MD5, checked too and replaced by bcrypt when the password is set again. A user with any
other hash (SHA-1, crypt, plain text) cannot log in until re-added. The file is
:func:`login_file`: :data:`SYSTEM_LOGIN_FILE` when it exists, else :func:`user_login_file`.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
from pathlib import Path

import bcrypt

from ..paths import invoking_home

SYSTEM_LOGIN_FILE = Path("/etc/rodeo-builder/htpasswd")
USER_RE = re.compile(r"^[A-Za-z0-9_.@][A-Za-z0-9_.@-]{0,254}$")
BCRYPT_RE = re.compile(r"^\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}$")
APR1_RE = re.compile(r"^\$apr1\$([./A-Za-z0-9]{1,8})\$[./A-Za-z0-9]{22}$")
_ITOA64 = b"./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BCRYPT_ROUNDS = 12
MAX_PASSWORD_BYTES = 72
MIN_PASSWORD = 8
_DUMMY_HASH = b"$2b$12$/dPy6oYsp1n3mdhjlX5hGu/7tG14QKAy/HnYWsW0dqnq5IRM6QWTK"


class LoginUnavailable(Exception):
    """No usable login file: missing, empty, or the user's hash is one rodeo cannot check."""


def user_login_file() -> Path:
    return invoking_home() / ".rodeo" / "builder" / "htpasswd"


def login_file() -> Path:
    """The login file in use: the system one when it exists, else the invoking user's."""
    return SYSTEM_LOGIN_FILE if SYSTEM_LOGIN_FILE.exists() else user_login_file()


def parse_basic(header: str) -> tuple[str, str] | None:
    """``(user, password)`` of a Basic *header*, or None when it is not one."""
    if not isinstance(header, str) or not header.startswith("Basic "):
        return None
    try:
        user, sep, password = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return (user, password) if sep and USER_RE.match(user) else None


def read_users(path: Path) -> dict[str, str]:
    """``{user: hash}`` of the htpasswd file *path*; blank and ``#`` lines are skipped."""
    users: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        user, sep, digest = line.partition(":")
        if sep and USER_RE.match(user):
            users[user] = digest
    return users


def is_bcrypt(digest: str) -> bool:
    return bool(BCRYPT_RE.match(digest))


def hash_kind(digest: str) -> str:
    """``"bcrypt"``, ``"apr1-md5"`` or ``"other"`` (a hash rodeo does not check)."""
    if is_bcrypt(digest):
        return "bcrypt"
    return "apr1-md5" if APR1_RE.match(digest) else "other"


def _to64(value: int, count: int) -> bytes:
    out = b""
    for _ in range(count):
        out += _ITOA64[value & 0x3F:(value & 0x3F) + 1]
        value >>= 6
    return out


def apr1_md5(password: bytes, salt: bytes) -> str:
    """Apache's ``$apr1$`` MD5 hash of *password* with *salt* (what ``htpasswd -m`` writes)."""
    magic = b"$apr1$"
    ctx = hashlib.md5(password + magic + salt)
    alt = hashlib.md5(password + salt + password).digest()
    left = len(password)
    while left > 0:
        ctx.update(alt[:min(16, left)])
        left -= 16
    i = len(password)
    while i:
        ctx.update(b"\0" if i & 1 else password[:1])
        i >>= 1
    final = ctx.digest()
    for i in range(1000):
        ctx = hashlib.md5(password if i & 1 else final)
        if i % 3:
            ctx.update(salt)
        if i % 7:
            ctx.update(password)
        ctx.update(final if i & 1 else password)
        final = ctx.digest()
    encoded = (_to64((final[0] << 16) | (final[6] << 8) | final[12], 4)
               + _to64((final[1] << 16) | (final[7] << 8) | final[13], 4)
               + _to64((final[2] << 16) | (final[8] << 8) | final[14], 4)
               + _to64((final[3] << 16) | (final[9] << 8) | final[15], 4)
               + _to64((final[4] << 16) | (final[10] << 8) | final[5], 4)
               + _to64(final[11], 2))
    return (magic + salt + b"$" + encoded).decode("ascii")


def verify(user: str, password: str, path: Path | None = None) -> bool:
    """True when *user* and *password* match the login file (*path*, default
    :func:`login_file`). Raises :class:`LoginUnavailable` when the file is missing or has
    no users, or when *user*'s hash is neither bcrypt nor APR1-MD5."""
    path = path or login_file()
    if not path.is_file():
        raise LoginUnavailable("logging in needs a user in {}: run rodeo logins add <user>".format(path))
    users = read_users(path)
    if not users:
        raise LoginUnavailable("{} has no users yet: run rodeo logins add <user>".format(path))
    if not USER_RE.match(user):
        return False
    secret = password.encode("utf-8")
    if len(secret) > MAX_PASSWORD_BYTES:
        return False
    digest = users.get(user)
    if digest is None:
        bcrypt.checkpw(secret, _DUMMY_HASH)
        return False
    kind = hash_kind(digest)
    if kind == "apr1-md5":
        salt = APR1_RE.match(digest).group(1).encode("ascii")
        return hmac.compare_digest(apr1_md5(secret, salt), digest)
    if kind != "bcrypt":
        raise LoginUnavailable("{} in {} has a hash rodeo cannot check (not bcrypt or APR1-MD5): set a new "
                               "password with rodeo logins add {}".format(user, path, user))
    return bcrypt.checkpw(secret, digest.encode("ascii"))


def hash_password(password: str) -> str:
    """A bcrypt hash of *password* (:data:`BCRYPT_ROUNDS`). Raises ValueError for a password
    shorter than :data:`MIN_PASSWORD` characters or longer than :data:`MAX_PASSWORD_BYTES` bytes."""
    secret = password.encode("utf-8")
    if len(password) < MIN_PASSWORD:
        raise ValueError("the password needs at least {} characters".format(MIN_PASSWORD))
    if len(secret) > MAX_PASSWORD_BYTES:
        raise ValueError("the password is longer than {} bytes, bcrypt's limit".format(MAX_PASSWORD_BYTES))
    return bcrypt.hashpw(secret, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def _write(path: Path, users: dict[str, str]) -> Path:
    """Writes *users* to *path* (through a symlink), keeping its mode and owner; a new file
    is 0600 in a 0700 directory. Returns the file written."""
    target = path.resolve() if path.exists() else path
    text = "".join("{}:{}\n".format(u, h) for u, h in users.items())
    tmp = target.with_name(target.name + ".new")
    if target.exists():
        st = target.stat()
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, st.st_mode & 0o777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent != SYSTEM_LOGIN_FILE.parent:
            target.parent.chmod(0o700)
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, 0o600)
    tmp.replace(target)
    return target


def set_user(user: str, password: str, path: Path | None = None) -> Path:
    """Adds *user* with *password* to the login file (*path*, default :func:`login_file`),
    replacing an existing line. Raises ValueError for a bad user name or password."""
    if not USER_RE.match(user):
        raise ValueError("a user name is letters, digits, '.', '_', '@' and '-', not starting with '-'")
    path = path or login_file()
    users = read_users(path) if path.is_file() else {}
    users[user] = hash_password(password)
    return _write(path, users)


def remove_user(user: str, path: Path | None = None) -> bool:
    """Removes *user* from the login file; False when it was not there."""
    path = path or login_file()
    users = read_users(path) if path.is_file() else {}
    if user not in users:
        return False
    del users[user]
    _write(path, users)
    return True


def list_users(path: Path | None = None) -> list[tuple[str, str]]:
    """``(user, kind of its hash)`` (:func:`hash_kind`) for every user of the login file."""
    path = path or login_file()
    if not path.is_file():
        return []
    return [(u, hash_kind(h)) for u, h in read_users(path).items()]
