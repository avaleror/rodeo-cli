"""Root side of the rodeo-cli Settings page: lists, adds and deletes root's stored credentials.

Run as ``rodeo-builder-helper {list|add|delete}`` (``python -m rodeo.builder.settings``),
through ``sudo -n`` by the page's CGI script (``cgi-bin/settings.py`` next to this file).
The request is a JSON object on stdin:

- ``list``: ``{}``; answers ``types``, ``items`` (rodeo's, :func:`.vault.list_items`,
  then other engines', :func:`.engine_credentials.list_items`) and ``file_secrets``
  (the keys of ``~/.rodeo/secrets.yaml``, never their values);
- ``add``: ``{name, type, fields, password}``, into rodeo's store; answers ``item``;
- ``delete``: ``{id, password}``; a credential of rodeo's store is removed only when
  *password* opens it. Failed passwords wait longer from the fifth in a row on, counted
  in :data:`ATTEMPTS` (:class:`.vault.OpenLimiter`); other engines' are read-only.

The answer, on stdout, is ``{"status": <HTTP status>, "body": {...}}``; no credential value
is ever written to it. The store is the one of the user the helper runs as (root under sudo),
the same ``~/.rodeo/vault/`` the builder reads.
"""
from __future__ import annotations

import json
import os
import pwd
import sys
from typing import Any

MAX_REQUEST = 1024 * 1024
ATTEMPTS = ".attempts.json"


def run(action: str, req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Carry out *action* with request *req*; returns ``(HTTP status, body)``."""
    from .. import deploywork, engine_credentials, vault

    try:
        if action == "list":
            return 200, {"types": vault.types(), "items": vault.list_items() + engine_credentials.list_items(),
                         "file_secrets": deploywork.file_secret_keys()}
        if action == "add":
            return 200, {"item": vault.add(req.get("name"), req.get("type"), req.get("fields"),
                                           req.get("password"))}
        if action == "delete":
            item_id = req.get("id")
            if engine_credentials.is_engine_id(item_id):
                return 403, {"error": "credentials of another engine are read-only in rodeo"}
            if not isinstance(item_id, str) or not vault.ID_RE.match(item_id):
                raise ValueError("bad credential id")
            limiter = vault.OpenLimiter(path=vault.vault_dir() / ATTEMPTS)
            limiter.check(item_id)
            try:
                vault.delete(item_id, req.get("password"))
            except vault.WrongPassword:
                limiter.failed(item_id)
                raise
            limiter.succeeded(item_id)
            return 200, {"ok": True}
    except vault.TooManyAttempts as exc:
        return 429, {"error": str(exc)}
    except vault.WrongPassword:
        return 403, {"error": "wrong password"}
    except FileNotFoundError:
        return 404, {"error": "unknown credential"}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return 400, {"error": "unknown action {!r}".format(action)}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    os.environ.pop("SUDO_USER", None)
    os.environ["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise ValueError("request too large")
        req = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(req, dict):
            raise ValueError("send a JSON object")
        status, body = run(args[0] if len(args) == 1 else "", req)
    except ValueError as exc:
        status, body = 400, {"error": str(exc)}
    except ImportError:
        status, body = 501, {"error": "storing credentials needs the cryptography package"}
    json.dump({"status": status, "body": body}, sys.stdout)
    return 0
