"""Where a remote host gets its rodeo-cli from — install URL, git ref, bootstrap.

Both remote paths bootstrap a host with ``install.sh``: single-host AWS
(``rodeo up --target aws``, see ``providers/remote_up.py``) and fleet deploy
(``rodeo fleet deploy``, see ``fleet/bootstrap.py``). They shared a bug and
would have drifted apart if fixed twice, so the fragment lives here once.
"""
from __future__ import annotations

import re
import shlex
from typing import Any, Mapping

from .config import ConfigError

INSTALL_URL_TEMPLATE = "https://raw.githubusercontent.com/avaleror/rodeo-cli/{ref}/install.sh"
DEFAULT_REF = "main"
DEFAULT_INSTALL_URL = INSTALL_URL_TEMPLATE.format(ref=DEFAULT_REF)

# A ref is interpolated into both a shell command and a raw.githubusercontent
# URL. Branch names legitimately carry "/" and "."; nothing else is worth
# accepting, and a leading "-" would be read as a flag by install.sh.
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def install_url_for_ref(ref: str) -> str:
    """Raw ``install.sh`` URL at *ref*.

    The installer is fetched from the same ref it is asked to check out, so
    install.sh can never disagree with the code it installs.
    """
    return INSTALL_URL_TEMPLATE.format(ref=ref)


def validate_ref(ref: str) -> str:
    """Reject refs that would be unsafe or meaningless in a URL path."""
    clean = ref.strip()
    if not clean or ".." in clean or not _REF_RE.match(clean):
        raise ConfigError(
            f"invalid rodeo-cli ref {ref!r}: use a branch, tag or SHA "
            "(letters, digits, '.', '_', '-', '/')"
        )
    return clean


def resolve_install_source(
    cfg: Mapping[str, Any],
    *,
    ref: str | None = None,
) -> tuple[str, str | None]:
    """``(install_url, ref)`` for the remote bootstrap.

    *cfg* is the block that may carry ``install_url`` / ``ref`` — an AWS
    ``provider:`` block or a fleet ``lab:`` block. An explicit ``install_url``
    is honoured verbatim: someone pointing at a fork or an air-gapped mirror
    means it. An explicit *ref* beats ``cfg["ref"]``; with neither, the host
    keeps whatever it already has.
    """
    chosen = str(ref or cfg.get("ref") or "").strip() or None
    if chosen is not None:
        chosen = validate_ref(chosen)
    configured = str(cfg.get("install_url") or "").strip()
    if configured:
        return configured, chosen
    if chosen is not None:
        return install_url_for_ref(chosen), chosen
    return DEFAULT_INSTALL_URL, None


def bootstrap_fragment(*, install_url: str, ref: str | None = None) -> str:
    """Shell fragment that leaves ``rodeo`` on PATH (no trailing separator).

    With a *ref*, the installer runs **every time** and install.sh hard-resets
    its checkout to that ref. Guarding on ``command -v rodeo`` is what made a
    pushed commit invisible to an already-bootstrapped host: the deploy
    silently ran whatever code the host was first installed with, so a live
    run could "pass" against stale code.

    Without a ref the guard stays: don't move a pinned host's version unasked
    — the same stance as ``clean --refresh``.
    """
    url = shlex.quote(install_url)
    if ref:
        install = f"curl -fsSL {url} | bash -s -- --ref {shlex.quote(ref)}; "
    else:
        install = (
            "if ! command -v rodeo >/dev/null 2>&1; then "
            f"curl -fsSL {url} | bash; "
            "fi; "
        )
    return f"{install}command -v rodeo >/dev/null"
