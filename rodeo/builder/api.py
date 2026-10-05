"""Request dispatch for the Rodeo Builder page (one place for every transport).

``Api(...).dispatch(action, method, params, body)`` returns ``(status, body)``.
The static build calls it for every GET the page makes and embeds the answers;
a live server would call it per request.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import discovery


class Api:
    def __init__(self, labinabox_checkout: Path | None = None,
                 lab_builder_url: str = discovery.LAB_BUILDER_URL, labinabox_version: str = ""):
        self.labinabox_checkout = labinabox_checkout
        self.lab_builder_url = lab_builder_url
        self.labinabox_version = labinabox_version

    def _get(self, action: str) -> dict[str, Any]:
        if action == "engines":
            return discovery.engines()
        if action == "workshops":
            return discovery.workshops()
        if action == "labinabox":
            return discovery.labinabox(self.labinabox_checkout, self.lab_builder_url,
                                       self.labinabox_version)
        raise KeyError(action)

    def dispatch(self, action: str, method: str = "GET", params: dict | None = None,
                 body: bytes = b"") -> tuple[int, dict[str, Any]]:
        if method != "GET":
            return 405, {"error": "{} {} is not supported".format(method, action)}
        try:
            return 200, self._get(action)
        except KeyError:
            return 404, {"error": "unknown action: {}".format(action)}
        except (OSError, RuntimeError, ValueError) as exc:
            return 500, {"error": str(exc)}

    def static_data(self) -> dict[str, Any]:
        """Every GET answer the page needs, keyed by action. Raises on any failure."""
        out = {}
        for action in ("engines", "workshops", "labinabox"):
            status, body = self.dispatch(action)
            if status != 200:
                raise RuntimeError("{}: {}".format(action, body.get("error")))
            out[action] = body
        return out
