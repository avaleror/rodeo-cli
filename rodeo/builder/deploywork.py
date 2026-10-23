"""Deploy a rodeo from the live Rodeo Builder without saving it to the profiles.

:meth:`Deployer.start` lays the page's files over the base profile (the same
check and layout as Save, :func:`rodeo.profile_install.write_rodeo`) into a new
lab directory, ``~/rodeo-labs/<name>-unsaved-<UTC time>/``, never into
``~/.rodeo/profiles/``, and runs ``rodeo up --yes --no-tmux --dir <lab>`` there
in the background, its output in ``<lab>/.builder-deploy.log``. The lab
directory is a copy: nothing on the page is saved by deploying.

``rodeo up`` needs root: :func:`capability` allows deploying only when the
builder runs as root or ``sudo -n true`` works. The plan's operator secrets
(values only the operator has) must already be in ``~/.rodeo/secrets.yaml`` or
come with the request; they are written there like ``rodeo up`` does. One
deploy runs at a time.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from ..config import LAB_SECRETS_FILE
from ..labseed import resolve_profile_source
from ..paths import invoking_home
from ..privilege import find_rodeo_bin
from ..profile_install import ProfileFile, check_files, check_name, write_rodeo
from ..secretgen import append_secret, ensure_plan_secrets, secrets_path

LOG = ".builder-deploy.log"
LOG_TAIL = 80
NEEDS_ROOT = ("rodeo up needs root: start rodeo builder as root (sudo rodeo builder ...) or allow this user "
              "passwordless sudo")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class NeedSecrets(Exception):
    """The plan needs operator secrets the request did not carry (``.keys``)."""

    def __init__(self, keys: list[str]):
        super().__init__("this lab needs secrets only you can provide: " + ", ".join(keys))
        self.keys = keys


def labs_root() -> Path:
    return invoking_home() / "rodeo-labs"


def capability(run: Run = subprocess.run) -> dict[str, Any]:
    """``{"available", "reason", "as"}``: whether ``rodeo up`` can get root from here ("root" or "sudo")."""
    if os.geteuid() == 0:
        return {"available": True, "reason": "", "as": "root"}
    if shutil.which("sudo"):
        try:
            r = run(["sudo", "-n", "true"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            if r.returncode == 0:
                return {"available": True, "reason": "", "as": "sudo"}
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"available": False, "reason": NEEDS_ROOT, "as": ""}


def _tail(path: Path, lines: int = LOG_TAIL) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


class Deployer:
    """The builder's one deploy: start, status, stop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: dict[str, Any] | None = None

    def status(self) -> dict[str, Any] | None:
        """``{"name", "lab", "started", "status", "rc", "log"}`` of the last deploy, or None.
        *status* is running, succeeded, failed or stopped."""
        with self._lock:
            cur = self._current
            if cur is None:
                return None
            rc = cur["proc"].poll()
            if rc is not None and cur["status"] in ("running", "stopping"):
                cur["status"] = "stopped" if cur["status"] == "stopping" else ("succeeded" if rc == 0 else "failed")
                cur["rc"] = rc
            return {"name": cur["name"], "lab": str(cur["lab"]), "started": cur["started"], "status": cur["status"],
                    "rc": cur["rc"], "log": _tail(cur["lab"] / LOG)}

    def start(self, req: Any, popen: Callable[..., Any] = subprocess.Popen, run: Run = subprocess.run) -> dict[str, Any]:
        """Deploy ``{"name", "base", "files", "secrets"}``. ValueError for a bad request, PermissionError
        without root, NeedSecrets for missing operator secrets, RuntimeError while another deploy runs."""
        if not isinstance(req, dict):
            raise ValueError("send a JSON object")
        state = self.status()
        if state and state["status"] in ("running", "stopping"):
            raise RuntimeError(f"a deploy is already running ({state['lab']}): wait for it or stop it")
        cap = capability(run)
        if not cap["available"]:
            raise PermissionError(cap["reason"])
        name = check_name(str(req.get("name") or ""))
        try:
            files = [ProfileFile(str(f["path"]), str(f["content"]).encode("utf-8"), bool(f.get("executable")))
                     for f in req.get("files") or []]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"bad file list: {exc}") from exc
        check_files(files)
        secrets = req.get("secrets") or {}
        if not isinstance(secrets, dict):
            raise ValueError("secrets must map names to values")
        src = resolve_profile_source(str(req["base"])) if req.get("base") else None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        labs_root().mkdir(parents=True, exist_ok=True)
        lab = labs_root() / f"{name}-unsaved-{stamp}"
        n = 2
        while lab.exists():
            lab = labs_root() / f"{name}-unsaved-{stamp}-{n}"
            n += 1
        try:
            write_rodeo(lab, name, files, src)
            plan = yaml.safe_load((lab / "rodeo-plan.yaml").read_text()) or {}
            _, missing = ensure_plan_secrets(plan, lab_path=lab / LAB_SECRETS_FILE)
            lacking = [k for k in missing if not str(secrets.get(k) or "")]
            if lacking:
                raise NeedSecrets(lacking)
        except BaseException:
            shutil.rmtree(lab, ignore_errors=True)
            raise
        for key in missing:
            append_secret(secrets_path(), key, str(secrets[key]))
        log = (lab / LOG).open("w")
        proc = popen([find_rodeo_bin(), "up", "--yes", "--no-tmux", "--dir", str(lab)], cwd=str(lab),
                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        log.close()
        with self._lock:
            self._current = {"name": name, "lab": lab, "proc": proc, "status": "running", "rc": None,
                             "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        return self.status() or {}

    def stop(self) -> dict[str, Any] | None:
        """Interrupt the running deploy (SIGINT to its process group, like Ctrl-C)."""
        with self._lock:
            cur = self._current
            if cur and cur["status"] == "running" and cur["proc"].poll() is None:
                try:
                    os.killpg(cur["proc"].pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
                cur["status"] = "stopping"
        return self.status()
