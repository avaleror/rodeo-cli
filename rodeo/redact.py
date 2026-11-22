"""Mask secret values in text that leaves rodeo's hands (event sinks, event logs).

Two sources feed the mask, both read at mask time, so a secret that becomes
known after a redactor was built is still masked:

- a process-wide registry: every value a ``??`` placeholder resolved to, every
  value loaded from a secrets file, and every secret rodeo generates
  (:func:`register_secret`);
- the config's own literal values under credential keys (``credentials.*``,
  ``*_password``, ``*token``, ...).

Values shorter than :data:`MIN_SECRET_LEN` are not masked: they would blank out
ordinary words. ``user:password@`` in a URL is always masked. This is a filter
for known values, not a guarantee: a secret transformed by a tool (base64, a
hash, split across lines) passes through.
"""
from __future__ import annotations

import re
import threading
from typing import Any

MASK = "***"
MIN_SECRET_LEN = 4

# Same key shapes labimport treats as credentials.
_SECRET_KEY_RE = re.compile(
    r"(^|_)(pass|password|passwd|pwd|pwd_hash|token|regcode|secret|secret_key|private_key|api_keys?)$",
    re.IGNORECASE,
)
# ":/+" also catches a URL that went through pathlib, which folds "//" into "/".
_URL_USERINFO = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*:/+)[^/@\s]+@")

_lock = threading.Lock()
_registry: set[str] = set()


def register_secret(value: Any) -> None:
    """Remember ``value`` (and each of its lines) as a secret for every later mask."""
    if value is None or isinstance(value, (bool, dict, list)):
        return
    text = str(value).strip()
    parts = {text, *(line.strip() for line in text.splitlines())}
    with _lock:
        _registry.update(p for p in parts if len(p) >= MIN_SECRET_LEN)


def register_secrets(mapping: Any) -> None:
    """Register every scalar in a secrets-file mapping."""
    if isinstance(mapping, dict):
        for v in mapping.values():
            if isinstance(v, (dict, list)):
                register_secrets(v)
            else:
                register_secret(v)
    elif isinstance(mapping, list):
        for v in mapping:
            register_secrets(v)


def registered_secrets() -> frozenset[str]:
    with _lock:
        return frozenset(_registry)


def config_secret_values(cfg: Any, _under: bool = False) -> set[str]:
    """Literal values under credential keys anywhere in ``cfg``."""
    out: set[str] = set()
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            secretish = _under or k == "credentials" or bool(_SECRET_KEY_RE.search(str(k)))
            if isinstance(v, (dict, list)):
                out |= config_secret_values(v, secretish)
            elif secretish and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                text = str(v)
                if len(text) >= MIN_SECRET_LEN and not text.startswith("??"):
                    out.add(text)
    elif isinstance(cfg, list):
        for v in cfg:
            out |= config_secret_values(v, _under)
    return out


def mask(text: str, values: frozenset[str] | set[str]) -> str:
    """``text`` with URL userinfo and every value in ``values`` replaced by ***."""
    text = _URL_USERINFO.sub(r"\1" + MASK + "@", text)
    for value in sorted(values, key=len, reverse=True):
        if value in text:
            text = text.replace(value, MASK)
    return text


class Redactor:
    """Masks the config's credential literals plus the live registry."""

    def __init__(self, cfg: Any = None) -> None:
        self._cfg_values = frozenset(config_secret_values(cfg)) if cfg is not None else frozenset()

    def values(self) -> frozenset[str]:
        return self._cfg_values | registered_secrets()

    def __call__(self, text: str) -> str:
        return mask(text, self.values())
