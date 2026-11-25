"""Who may open this lab: one access token, then signed per-host cookies.

The lab has one student. Whoever opens ``https://<player>/enter?t=<token>``
gets a cookie for the player name. Caddy asks the player (``forward_auth`` to
``/_auth``) before every request on all three names, terminal and websockets
included. The two UI names (``hv.`` and ``rancher.``) are separate hosts and
cannot share the player's cookie without putting it on the parent domain,
where every other lab would receive it, so they get their own host-only
cookie through a short hop: no cookie on ``hv.`` -> redirect to the player's
``/auth/hop`` -> the player (which sees its own cookie) redirects back to
``hv.<base>/.rodeo/auth`` with a 60-second signed ticket -> cookie set.

Cookies and tickets are HMAC-SHA256 over ``host|expiry`` with a per-lab
secret, so a cookie from one lab or one name is useless on another.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

COOKIE = "rodeo_player"
SESSION_SECONDS = 24 * 3600
TICKET_SECONDS = 60


@dataclass(frozen=True)
class Auth:
    secret: bytes
    token: str

    # -- token -------------------------------------------------------------

    def token_ok(self, candidate: str) -> bool:
        return bool(candidate) and hmac.compare_digest(candidate.encode(), self.token.encode())

    # -- signed values -----------------------------------------------------

    def _sign(self, kind: str, host: str, exp: int) -> str:
        mac = hmac.new(self.secret, f"{kind}|{host}|{exp}".encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{mac}"

    def _verify(self, kind: str, host: str, value: str, now: float | None = None) -> bool:
        try:
            exp_s, mac = value.split(".", 1)
            exp = int(exp_s)
        except (ValueError, AttributeError):
            return False
        if exp < (now if now is not None else time.time()):
            return False
        return hmac.compare_digest(self._sign(kind, host, exp), f"{exp}.{mac}")

    def cookie(self, host: str, now: float | None = None) -> str:
        return self._sign("cookie", host, int((now or time.time()) + SESSION_SECONDS))

    def cookie_ok(self, host: str, value: str, now: float | None = None) -> bool:
        return self._verify("cookie", host, value, now)

    def ticket(self, host: str, now: float | None = None) -> str:
        return self._sign("ticket", host, int((now or time.time()) + TICKET_SECONDS))

    def ticket_ok(self, host: str, value: str, now: float | None = None) -> bool:
        return self._verify("ticket", host, value, now)


def load(path: Path | str) -> Auth:
    data = json.loads(Path(path).read_text())
    return Auth(secret=bytes.fromhex(data["secret"]), token=str(data["token"]))


def ensure(path: Path | str) -> Auth:
    """Create the lab's secret and token once (root-only); keep them on re-runs."""
    path = Path(path)
    if not path.exists():
        data = {"secret": secrets.token_hex(32), "token": secrets.token_urlsafe(24)}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
    os.chmod(path, 0o600)
    return load(path)


def rotate(path: Path | str) -> Auth:
    """New token and secret: every link and cookie handed out so far stops working."""
    Path(path).unlink(missing_ok=True)
    return ensure(path)
