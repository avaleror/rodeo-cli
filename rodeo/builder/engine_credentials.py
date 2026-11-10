"""Credentials the lab-in-a-box engine keeps in its own store, offered next to rodeo's.

The store is :data:`rodeo.labinabox_host.CREDENTIALS_DIR`, one YAML or JSON file per
credential (root only). rodeo lists and opens them, and never adds, changes or deletes
them. Each one is listed as a ``generic`` credential (``engine: lab-in-a-box``) whose
fields are the file's keys, lowercased (``AWS_ACCESS_KEY_ID`` fills the plan secret
``aws_access_key_id``), without the file's own markers. A file is one of:

- ``unencrypted: true`` or with no encrypted value: opened without a password;
- ``encrypted: true`` at the top: the whole mapping is one envelope;
- some values are envelopes (``{encrypted: true, ...}``): each is decrypted.

Envelopes are decrypted by the engine's own ``crypto_store`` module (``libs/`` of its
checkout: the one given, ``RODEO_LABINABOX_PATH``, or the newest under
``~/.rodeo/vendor/lab-in-a-box/``), with the credential's passphrase.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from .. import labinabox_host
from ..paths import rodeo_dir
from .vault import WrongPassword

ENGINE = "lab-in-a-box"
PREFIX = "liab-"
ID_RE = re.compile(r"^liab-[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EXTS = (".yaml", ".yml", ".json")
MARKERS = {"cloudtype", "cloud_type", "credential_kind", "kind", "unencrypted", "encrypted", "schema_version"}


def store_dir() -> Path:
    return labinabox_host.CREDENTIALS_DIR


def _load(path: Path) -> dict[str, Any]:
    import yaml

    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("{} is not a mapping".format(path.name))
    return data


def _is_envelope(value: Any) -> bool:
    return isinstance(value, dict) and value.get("encrypted") is True


def _files() -> list[Path]:
    directory = store_dir()
    try:
        return sorted(p for p in directory.iterdir()
                      if p.is_file() and p.suffix in EXTS and ID_RE.match(PREFIX + p.stem))
    except OSError:
        return []


def list_items() -> list[dict[str, Any]]:
    """Metadata of every credential in the store (as :func:`.vault.list_items`, plus
    ``encrypted``: whether opening it needs a password, and ``app``: the provider or
    service it is for, the part of the file name ``<app>-<account>.yaml`` before the
    first ``-``), never a value. Unreadable or unparsable files are left out."""
    out = []
    for path in _files():
        try:
            data = _load(path)
            created = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:  # noqa: BLE001 - OSError, ValueError, yaml.YAMLError: a file it cannot read is skipped
            continue
        encrypted = data.get("encrypted") is True or any(_is_envelope(v) for v in data.values())
        out.append({"id": PREFIX + path.stem, "name": path.stem, "type": "generic", "created": created,
                    "used": None, "engine": ENGINE, "encrypted": encrypted,
                    "app": path.stem.split("-", 1)[0]})
    return out


def _path(item_id: Any) -> Path:
    if not isinstance(item_id, str) or not ID_RE.match(item_id):
        raise ValueError("bad credential id")
    stem = item_id[len(PREFIX):]
    for ext in EXTS:
        path = store_dir() / (stem + ext)
        if path.is_file():
            return path
    raise FileNotFoundError(item_id)


def is_engine_id(item_id: Any) -> bool:
    return isinstance(item_id, str) and item_id.startswith(PREFIX)


def _checkouts(checkout: Path | None) -> list[Path]:
    found = [c for c in (checkout, labinabox_host.local_override()) if c is not None]
    vendor = rodeo_dir() / "vendor" / "lab-in-a-box"
    if vendor.is_dir():
        found += sorted((d for d in vendor.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
    return found


def _crypto(checkout: Path | None) -> ModuleType:
    for root in _checkouts(checkout):
        path = root / "libs" / "crypto_store.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("rodeo_labinabox_crypto_store", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise ValueError("opening an encrypted lab-in-a-box credential needs a lab-in-a-box checkout "
                     "(rodeo downloads one for a lab-in-a-box lab)")


def _decrypt(crypto: ModuleType, envelope: dict[str, Any], password: str) -> bytes:
    try:
        return crypto.decrypt_cascade(password if isinstance(password, str) else "", envelope)
    except crypto.DecryptionError:
        raise WrongPassword("wrong password") from None
    except RuntimeError as exc:
        raise ValueError(str(exc)) from None


def open_item(item_id: str, password: str, checkout: Path | None = None) -> dict[str, str]:
    """The credential's fields (lowercased keys, markers left out). Raises ValueError on a
    bad id or file, FileNotFoundError when unknown and :class:`.vault.WrongPassword` when
    *password* does not decrypt it; an unencrypted file ignores *password*."""
    import yaml

    path = _path(item_id)
    try:
        data = _load(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("cannot read {}: {}".format(path.name, exc)) from None
    except Exception as exc:  # noqa: BLE001 - yaml.YAMLError: a damaged file
        raise ValueError("damaged credential file {}: {}".format(path.name, exc)) from None
    if data.get("encrypted") is True:
        inner = yaml.safe_load(_decrypt(_crypto(checkout), data, password).decode("utf-8"))
        if not isinstance(inner, dict):
            raise ValueError("{} decrypts to something that is not a mapping".format(path.name))
        data = inner
    elif any(_is_envelope(v) for v in data.values()):
        crypto = _crypto(checkout)
        data = {k: _decrypt(crypto, v, password).decode("utf-8") if _is_envelope(v) else v for k, v in data.items()}
    return {str(k).lower(): "" if v is None else str(v) for k, v in data.items()
            if str(k).lower() not in MARKERS and not isinstance(v, (dict, list))}
