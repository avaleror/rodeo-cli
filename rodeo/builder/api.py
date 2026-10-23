"""Request dispatch for the Rodeo Builder page (one place for every transport).

``Api(...).dispatch(action, method, params, body)`` returns ``(status, body)``.
The static build calls it for every GET the page makes and embeds the answers;
the ``rodeo builder`` server calls it per request.

GET actions: ``engines``, ``workshops``, ``labinabox`` (the page's data, also
embedded by the static build), ``server`` (what the live server can do) and
``story`` (rmstory and its engines on the server's host) and ``github`` (the GitHub
login Publish would use, see :mod:`.publish`).
POST ``save`` writes a rodeo to ``~/.rodeo/profiles/<name>/``; POST ``validate``,
``translate`` and ``draft`` run rmstory (see :mod:`.storywork`); POST ``import``
reads a workshop's chapters from a git URL (see :mod:`.imports`); POST ``publish``
and ``github-login`` publish to GitHub with the host's login. POST actions answer
only when the Api was created with ``can_save=True`` (the live server); ``github``,
``publish`` and ``github-login`` also need ``can_publish=True``, and GET ``deploy``
with POST ``deploy`` and ``deploy-stop`` (see :mod:`.deploywork`) ``can_deploy=True``:
a server that listens on loopback only, so nobody else can act as its user.
GET ``vault`` with POST ``vault-add``, ``vault-delete`` and ``vault-open`` (the
encrypted credential store, see :mod:`.vault`) also need ``can_deploy=True``;
GET ``vault`` answers ``available: false`` without it or without the
``cryptography`` package. Its ``file_secrets`` lists the keys of
``~/.rodeo/secrets.yaml`` (never their values) that POST ``deploy`` can name in
``secret_refs``; ``[]`` without ``can_deploy=True``. When available, ``user`` is
``<login>@<host>``: whose ``~/.rodeo`` the credentials live in.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from ..paths import invoking_user
from . import deploywork, discovery, imports, publish, storywork

try:
    from . import vault
except ImportError:
    vault = None  # type: ignore[assignment]

PAGE_ACTIONS = ("engines", "workshops", "labinabox")
STORY_ACTIONS = ("validate", "translate", "draft", "retell")
GITHUB_ACTIONS = ("publish", "github-login")
DEPLOY_ACTIONS = ("deploy", "deploy-stop")
VAULT_ACTIONS = ("vault-add", "vault-delete", "vault-open")
LIVE_ACTIONS = ("save", "import", *STORY_ACTIONS, *GITHUB_ACTIONS, *DEPLOY_ACTIONS, *VAULT_ACTIONS)
LOOPBACK_ONLY = "needs a rodeo builder that listens on this machine only"
NO_CRYPTOGRAPHY = "stored credentials need the Python package cryptography (pip install cryptography)"


class Api:
    def __init__(self, labinabox_checkout: Path | None = None,
                 lab_builder_url: str = discovery.LAB_BUILDER_URL, labinabox_version: str = "",
                 source_checkouts: dict[str, Path] | None = None, can_save: bool = False,
                 labinabox_missing: str = "", can_publish: bool = False, can_deploy: bool = False):
        self.labinabox_checkout = labinabox_checkout
        self.labinabox_missing = labinabox_missing
        self.source_checkouts = source_checkouts or {}
        self.lab_builder_url = lab_builder_url
        self.labinabox_version = labinabox_version
        self.can_save = can_save
        self.can_publish = can_save and can_publish
        self.can_deploy = can_save and can_deploy
        self.deployer = deploywork.Deployer()
        self.vault_limiter = vault.OpenLimiter() if vault is not None else None

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
        if action == "story":
            return storywork.info()
        if action == "deploy":
            if not self.can_deploy:
                return {"available": False, "reason": "deploying " + LOOPBACK_ONLY, "as": "", "current": None}
            return {**deploywork.capability(), "current": self.deployer.status()}
        if action == "github":
            return {**publish.status(), "available": True} if self.can_publish else {"available": False}
        if action == "vault":
            if not self.can_deploy:
                return {"available": False, "reason": "stored credentials " + LOOPBACK_ONLY,
                        "types": [], "items": [], "file_secrets": []}
            if vault is None:
                return {"available": False, "reason": NO_CRYPTOGRAPHY, "types": [], "items": [],
                        "file_secrets": deploywork.file_secret_keys()}
            return {"available": True, "reason": "", "types": vault.types(), "items": vault.list_items(),
                    "file_secrets": deploywork.file_secret_keys(),
                    "user": "{}@{}".format(invoking_user(), socket.gethostname())}
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

    def _import(self, body: bytes) -> tuple[int, dict[str, Any]]:
        try:
            req = json.loads(body.decode("utf-8"))
            return 200, {"workshop": imports.import_source(str(req.get("url") or "") if isinstance(req, dict) else "")}
        except ValueError as exc:
            return 400, {"error": str(exc)}
        except (RuntimeError, OSError) as exc:
            return 502, {"error": str(exc)}

    def _deploy(self, action: str, body: bytes) -> tuple[int, dict[str, Any]]:
        if not self.can_deploy:
            return 403, {"error": "deploying " + LOOPBACK_ONLY}
        if action == "deploy-stop":
            return 200, {"current": self.deployer.stop()}
        try:
            return 200, {"current": self.deployer.start(json.loads(body.decode("utf-8")))}
        except deploywork.NeedSecrets as exc:
            return 200, {"current": None, "secrets": exc.keys, "message": str(exc)}
        except PermissionError as exc:
            return 403, {"error": str(exc)}
        except RuntimeError as exc:
            return 409, {"error": str(exc), "running": True}
        except (ValueError, FileNotFoundError) as exc:
            return 400, {"error": str(exc)}
        except OSError as exc:
            return 500, {"error": str(exc)}

    def _vault(self, action: str, body: bytes) -> tuple[int, dict[str, Any]]:
        if not self.can_deploy:
            return 403, {"error": "stored credentials " + LOOPBACK_ONLY}
        if vault is None or self.vault_limiter is None:
            return 501, {"error": NO_CRYPTOGRAPHY}
        try:
            req = json.loads(body.decode("utf-8"))
            if not isinstance(req, dict):
                raise ValueError("send a JSON object")
            if action == "vault-add":
                return 200, {"item": vault.add(req.get("name"), req.get("type"), req.get("fields"),
                                               req.get("password"))}
            item_id = req.get("id")
            if action == "vault-delete":
                vault.delete(item_id)
                return 200, {"ok": True}
            if not isinstance(item_id, str) or not vault.ID_RE.match(item_id):
                raise ValueError("bad credential id")
            self.vault_limiter.check(item_id)
            try:
                fields = vault.open_item(item_id, req.get("password"))
            except vault.WrongPassword:
                self.vault_limiter.failed(item_id)
                return 403, {"error": "wrong password"}
            self.vault_limiter.succeeded(item_id)
            return 200, {"fields": fields}
        except vault.TooManyAttempts:
            return 429, {"error": "too many attempts, wait a minute"}
        except FileNotFoundError:
            return 404, {"error": "unknown credential"}
        except ValueError as exc:
            return 400, {"error": str(exc)}
        except OSError as exc:
            return 500, {"error": str(exc)}

    def _github(self, action: str, body: bytes) -> tuple[int, dict[str, Any]]:
        if not self.can_publish:
            return 403, {"error": "publishing to GitHub needs a rodeo builder that listens on this machine only"}
        try:
            if action == "github-login":
                return 200, publish.start_login()
            return 200, publish.publish(json.loads(body.decode("utf-8")))
        except PermissionError as exc:
            return 401, {"error": str(exc), "login": True}
        except FileExistsError as exc:
            return 409, {"error": str(exc), "exists": True}
        except ValueError as exc:
            return 400, {"error": str(exc)}
        except (publish.PublishError, OSError) as exc:
            return 502, {"error": str(exc)}

    def _story(self, action: str, body: bytes) -> tuple[int, dict[str, Any]]:
        try:
            req = json.loads(body.decode("utf-8"))
            if not isinstance(req, dict):
                raise ValueError("send a JSON object")
            if action == "validate":
                return 200, storywork.validate(req.get("chapters"))
            if action == "translate":
                return 200, storywork.translate(req.get("chapters"), str(req.get("to") or ""),
                                                str(req.get("engine") or ""), req.get("strings"))
            if action == "retell":
                return 200, storywork.retell(req.get("chapters"), req.get("theme"), str(req.get("engine") or ""))
            return 200, storywork.draft(req.get("prompt"), str(req.get("lang") or "en"), str(req.get("engine") or ""))
        except ValueError as exc:
            return 400, {"error": str(exc)}
        except (storywork.StoryError, OSError) as exc:
            return 502, {"error": str(exc)}

    def dispatch(self, action: str, method: str = "GET", params: dict | None = None,
                 body: bytes = b"") -> tuple[int, dict[str, Any]]:
        if method == "POST" and action in LIVE_ACTIONS:
            if not self.can_save:
                what = {"save": "saving", "import": "importing a workshop", "deploy": "deploying",
                        "deploy-stop": "deploying", "publish": "publishing to GitHub",
                        "github-login": "publishing to GitHub", "vault-add": "storing credentials",
                        "vault-delete": "storing credentials",
                        "vault-open": "storing credentials"}.get(action, "rmstory " + action)
                return 403, {"error": "{} needs the rodeo builder server".format(what)}
            if action in GITHUB_ACTIONS:
                return self._github(action, body)
            if action in DEPLOY_ACTIONS:
                return self._deploy(action, body)
            if action in VAULT_ACTIONS:
                return self._vault(action, body)
            if action in STORY_ACTIONS:
                return self._story(action, body)
            if action == "import":
                return self._import(body)
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
