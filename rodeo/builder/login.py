"""Logins for the builder's credential writes (POST ``vault-add``, ``vault-delete``).

HTTP Basic (``Authorization: Basic <user:password>``), checked against the Settings
page's login file, :data:`LOGIN_FILE`, with Apache's own tool (``htpasswd -i -v``, the
password on stdin), so the builder accepts exactly the users the web server does.
"""
from __future__ import annotations

import base64
import binascii
import re
import shutil
import subprocess
from pathlib import Path

LOGIN_FILE = Path("/etc/rodeo-builder/htpasswd")
USER_RE = re.compile(r"^[A-Za-z0-9_.@][A-Za-z0-9_.@-]{0,254}$")


class LoginUnavailable(Exception):
    """No login file or no htpasswd tool on this host."""


def parse_basic(header: str) -> tuple[str, str] | None:
    """``(user, password)`` of a Basic *header*, or None when it is not one."""
    if not isinstance(header, str) or not header.startswith("Basic "):
        return None
    try:
        user, sep, password = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return (user, password) if sep and USER_RE.match(user) else None


def verify(user: str, password: str) -> bool:
    """True when *user* and *password* match :data:`LOGIN_FILE`. Raises
    :class:`LoginUnavailable` without that file or the htpasswd tool."""
    tool = shutil.which("htpasswd") or shutil.which("htpasswd2")
    if tool is None or not LOGIN_FILE.is_file():
        raise LoginUnavailable("logging in needs {} and Apache's htpasswd tool".format(LOGIN_FILE))
    if not USER_RE.match(user):
        return False
    r = subprocess.run([tool, "-i", "-v", str(LOGIN_FILE), user], input=password.encode("utf-8"),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    return r.returncode == 0
