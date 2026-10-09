"""Request dispatch for the Rodeo Builder page (one place for every transport).

``Api(...).dispatch(action, method, params, body)`` returns ``(status, body)``.
The static build calls it for every GET the page makes and embeds the answers;
the ``rodeo builder`` server calls it per request.

GET actions: ``engines``, ``workshops``, ``labinabox`` (the page's data, also
embedded by the static build) and ``server`` (what the live server can do).
POST ``save`` writes a rodeo to ``~/.rodeo/profiles/<name>/``; it answers only
when the Api was created with ``can_save=True`` (the live server).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import discovery

PAGE_ACTIONS = ("engines", "workshops", "labinabox")


class Api:
    def __init__(self, labinabox_checkout: Path | None = None,
                 lab_builder_url: str = discovery.LAB_BUILDER_URL, labinabox_version: str = "",
                 source_checkouts: dict[str, Path] | None = None, can_save: bool = False,
                 labinabox_missing: str = ""):
        self.labinabox_checkout = labinabox_checkout
        self.labinabox_missing = labinabox_missing
        self.source_checkouts = source_checkouts or {}
        self.lab_builder_url = lab_builder_url
        self.labinabox_version = labinabox_version
        self.can_save = can_save

    def _get(self, action: str) -> dict[str, Any]:
        if action == "engines":
            return discovery.engines()
        if action == "workshops":
            return discovery.workshops(self.source_checkouts)
        if action == "labinabox":
            return discovery.labinabox(self.labinabox_checkout, self.lab_builder_url,
                                       self.labinabox_version, self.labinabox_missing)
        if action == "server":
            return self._server()
        raise KeyError(action)

    def _server(self) -> dict[str, Any]:
        from ..labseed import custom_profiles_root, list_profiles

        return {"save": self.can_save, "profiles_dir": str(custom_profiles_root()),
                "profiles": [p["name"] for p in list_profiles() if p["kind"] == "custom"],
                "bundled": [p["name"] for p in list_profiles() if p["kind"] == "bundled"]}

    def _save(self, body: bytes) -> tuple[int, dict[str, Any]]:
        from ..profile_install import ProfileFile, install_profile

        try:
            req = json.loads(body.decode("utf-8"))
            files = [ProfileFile(str(f["path"]), str(f["content"]).encode("utf-8"), bool(f.get("executable")))
                     for f in req["files"]]
            name, base, force = str(req["name"]), req.get("base") or None, bool(req.get("force"))
        except (ValueError, KeyError, TypeError) as exc:
            return 400, {"error": "bad save request: {}".format(exc)}
        try:
            dest = install_profile(name, files, base=str(base) if base else None, force=force)
        except FileExistsError as exc:
            return 409, {"error": str(exc), "exists": True}
        except (FileNotFoundError, ValueError) as exc:
            return 400, {"error": str(exc)}
        return 200, {"path": str(dest), "profile": name, "files": len(files)}

    def dispatch(self, action: str, method: str = "GET", params: dict | None = None,
                 body: bytes = b"") -> tuple[int, dict[str, Any]]:
        if method == "POST" and action == "save":
            if not self.can_save:
                return 403, {"error": "saving needs the rodeo builder server"}
            try:
                return self._save(body)
            except OSError as exc:
                return 500, {"error": str(exc)}
        if method != "GET":
            return 405, {"error": "{} {} is not supported".format(method, action)}
        try:
            return 200, self._get(action)
        except KeyError:
            return 404, {"error": "unknown action: {}".format(action)}
        except (OSError, RuntimeError, ValueError) as exc:
            return 500, {"error": str(exc)}

    def static_data(self, require_labinabox: bool = True) -> dict[str, Any]:
        """Every GET answer the page needs, keyed by action. Raises on any failure,
        and when the lab-in-a-box catalogue is missing unless *require_labinabox* is off."""
        out = {}
        for action in PAGE_ACTIONS:
            status, body = self.dispatch(action)
            if status != 200:
                raise RuntimeError("{}: {}".format(action, body.get("error")))
            out[action] = body
        if require_labinabox and out["labinabox"]["error"]:
            raise RuntimeError("lab-in-a-box catalogue: {}".format(out["labinabox"]["error"]))
        return out
