"""Publish a rodeo from the live Rodeo Builder to GitHub with the user's own login.

The login already on the builder's host is reused, never stored: ``GH_TOKEN`` or
``GITHUB_TOKEN``, else ``gh auth token``, else git's stored github.com
credential (``git credential fill``, which never prompts here). With none,
:func:`start_login` runs ``gh auth login --web`` (GitHub's device flow) and the
page shows its one-time code; the next :func:`status` finds the new login.

:func:`publish` creates the public repository ``<login>/<name>``, pushes the
rodeo's files as one commit on ``main`` and opens a pull request on the catalog
repository (``RODEO_CATALOG_REPO``, default ``avaleror/rodeo-cli``) adding
``rodeo/data/catalog.d/<name>.yaml``. With *update*, the files replace the
existing repository's in a new commit and no pull request is opened.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from ..profile_install import ProfileFile, check_files, check_name

API = "https://api.github.com"
HOST = "github.com"
DEVICE_URL = "https://github.com/login/device"
CATALOG_REPO = "avaleror/rodeo-cli"
CATALOG_DIR = "rodeo/data/catalog.d"
TIMEOUT = 120
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$")
_CODE_RE = re.compile(r"one-time code: ([A-Z0-9]{4}-[A-Z0-9]{4})")
_ASKPASS = '!f() { echo username=x-access-token; echo "password=$RODEO_GH_TOKEN"; }; f'

Http = Callable[[str, str, str, "dict[str, Any] | None"], "tuple[int, Any]"]
Run = Callable[..., "subprocess.CompletedProcess[str]"]


class PublishError(RuntimeError):
    """GitHub or git refused or failed; the message says which step."""


def catalog_repo() -> str:
    return os.environ.get("RODEO_CATALOG_REPO") or CATALOG_REPO


def _http(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "rodeo-builder"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else {}
        except ValueError:
            return exc.code, {"message": raw.decode("utf-8", "replace")[:300]}
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PublishError("cannot reach GitHub: {}".format(getattr(exc, "reason", exc))) from exc


def _quiet_env() -> dict[str, str]:
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true", "GH_PROMPT_DISABLED": "1"}


def credentials(run: Run = subprocess.run) -> tuple[str, str]:
    """(token, where it came from: "GH_TOKEN", "GITHUB_TOKEN", "gh" or "git"), or ("", "")."""
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var], var
    if shutil.which("gh"):
        try:
            r = run(["gh", "auth", "token", "--hostname", HOST], capture_output=True, text=True, timeout=20, env=_quiet_env())
        except (OSError, subprocess.TimeoutExpired):
            r = None
        if r is not None and r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip(), "gh"
    try:
        r = run(["git", "credential", "fill"], input="protocol=https\nhost={}\n\n".format(HOST),
                capture_output=True, text=True, timeout=20, env=_quiet_env())
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    if r.returncode == 0:
        fields = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
        if fields.get("password"):
            return fields["password"], "git"
    return "", ""


_login_lock = threading.Lock()
_login: dict[str, Any] = {"proc": None, "code": "", "error": ""}


def login_state() -> dict[str, Any]:
    """``{"pending", "code", "url", "error"}`` of the device-flow login :func:`start_login` began."""
    with _login_lock:
        proc = _login["proc"]
        if proc is not None and proc.poll() is not None:
            if proc.returncode != 0 and not _login["error"]:
                _login["error"] = "gh auth login exited with {}".format(proc.returncode)
            _login["proc"] = None
        return {"pending": _login["proc"] is not None, "code": _login["code"],
                "url": DEVICE_URL if _login["code"] else "", "error": _login["error"]}


def status(http: Http = _http, run: Run = subprocess.run) -> dict[str, Any]:
    """``{"logged_in", "login", "source", "gh", "error", "device"}`` for the page's Publish dialog."""
    token, source = credentials(run)
    out: dict[str, Any] = {"logged_in": False, "login": "", "source": source, "gh": bool(shutil.which("gh")),
                           "error": "", "device": login_state(), "catalog": catalog_repo()}
    if not token:
        return out
    try:
        code, body = http("GET", API + "/user", token, None)
    except PublishError as exc:
        out["error"] = str(exc)
        return out
    if code != 200:
        out["error"] = "the GitHub login from {} was refused ({}): {}".format(source, code, _message(body))
        return out
    out.update(logged_in=True, login=str(body.get("login") or ""))
    return out


def start_login(popen: Callable[..., Any] = subprocess.Popen, wait: float = 20.0) -> dict[str, Any]:
    """Start ``gh auth login --web`` in the background and return :func:`login_state` once
    gh has printed its one-time code (the user enters it at :data:`DEVICE_URL`)."""
    if not shutil.which("gh"):
        raise PublishError("no GitHub login on this host and the GitHub CLI (gh) is not installed: install gh "
                           "and log in again, run gh auth login, or set GH_TOKEN before rodeo builder")
    with _login_lock:
        if _login["proc"] is not None and _login["proc"].poll() is None:
            return {"pending": True, "code": _login["code"], "url": DEVICE_URL, "error": ""}
        env = {**os.environ, "GH_BROWSER": "true", "BROWSER": "true", "GH_PROMPT_DISABLED": "1"}
        proc = popen(["gh", "auth", "login", "--hostname", HOST, "--git-protocol", "https", "--web"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        _login.update(proc=proc, code="", error="")
    found = threading.Event()

    def read() -> None:
        for line in proc.stdout:
            match = _CODE_RE.search(line)
            if match:
                with _login_lock:
                    _login["code"] = match.group(1)
                found.set()
        found.set()

    threading.Thread(target=read, daemon=True).start()
    found.wait(wait)
    state = login_state()
    if not state["code"] and not state["error"]:
        state["error"] = "gh auth login did not print a one-time code"
    return state


def _message(body: Any) -> str:
    if isinstance(body, dict):
        errors = "; ".join(str(e.get("message") or e) for e in body.get("errors") or [] if isinstance(e, (dict, str)))
        text = " ".join(p for p in (str(body.get("message") or ""), errors) if p) or "no message"
        if "not accessible by personal access token" in text:
            text += (" (this token may not do that: log in with gh auth login, or use a token allowed to create "
                     "repositories and pull requests)")
        return text
    return str(body)[:300]


def _call(http: Http, token: str, step: str, method: str, path: str, body: dict[str, Any] | None = None,
          ok: tuple[int, ...] = (200, 201)) -> Any:
    code, answer = http(method, API + path, token, body)
    if code not in ok:
        raise PublishError("{} (GitHub {}): {}".format(step, code, _message(answer)))
    return answer


def catalog_entry(name: str, title: str, repo: str, engine: str, languages: list[str]) -> str:
    """The catalog file ``rodeo/data/catalog.d/<name>.yaml``."""
    return yaml.safe_dump({"name": name, "title": title, "repo": repo, "ref": "main", "engine": engine,
                           "languages": languages}, sort_keys=False, allow_unicode=True)


def _git(run: Run, args: list[str], cwd: Path, token: str, step: str) -> None:
    env = {**_quiet_env(), "RODEO_GH_TOKEN": token}
    cmd = ["git", "-c", "credential.helper=", "-c", "credential.helper=" + _ASKPASS, "-c", "core.hooksPath=/dev/null", *args]
    try:
        r = run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise PublishError("{} took longer than {} s".format(step, TIMEOUT)) from exc
    if r.returncode != 0:
        raise PublishError("{}: {}".format(step, (r.stderr or r.stdout).strip()[-300:]).replace(token, "***"))


def _push(run: Run, files: list[ProfileFile], remote: str, author: tuple[str, str], token: str, message: str,
          update: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="rodeo-builder-publish-") as tmp:
        work = Path(tmp) / "repo"
        if update:
            _git(run, ["clone", "-q", "--depth", "1", "--", remote, str(work)], Path(tmp), token, "fetching " + remote)
            _git(run, ["rm", "-rq", "--ignore-unmatch", "."], work, token, "clearing the old files")
        else:
            work.mkdir()
            _git(run, ["init", "-q", "-b", "main"], work, token, "git init")
        for f in files:
            dest = work / f.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(f.data)
            dest.chmod(0o755 if f.executable else 0o644)
        _git(run, ["add", "-A"], work, token, "git add")
        _git(run, ["-c", "user.name=" + author[0], "-c", "user.email=" + author[1], "commit", "-q",
                   "--allow-empty", "-m", message], work, token, "git commit")
        _git(run, ["push", "-q", remote, "HEAD:main"], work, token, "pushing to " + remote)


def _open_catalog_pr(http: Http, token: str, login: str, name: str, entry: str, sleep: Callable[[float], None]) -> str:
    upstream = catalog_repo()
    owner = upstream.split("/", 1)[0]
    info = _call(http, token, "reading " + upstream, "GET", "/repos/" + upstream)
    base = str(info.get("default_branch") or "main")
    sha = _call(http, token, "reading " + upstream, "GET", "/repos/{}/git/ref/heads/{}".format(upstream, base))["object"]["sha"]
    if owner.lower() == login.lower():
        head_repo = upstream
    else:
        fork = _call(http, token, "forking " + upstream, "POST", "/repos/{}/forks".format(upstream), {}, ok=(200, 202))
        head_repo = str(fork.get("full_name") or "{}/{}".format(login, upstream.split("/", 1)[1]))
        for _ in range(30):
            if http("GET", API + "/repos/" + head_repo, token, None)[0] == 200:
                break
            sleep(2)
    branch = "catalog-{}-{}".format(name, datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
    _call(http, token, "creating branch " + branch, "POST", "/repos/{}/git/refs".format(head_repo),
          {"ref": "refs/heads/" + branch, "sha": sha})
    path = "{}/{}.yaml".format(CATALOG_DIR, name)
    put: dict[str, Any] = {"message": "catalog: add {}".format(name), "branch": branch,
                           "content": base64.b64encode(entry.encode("utf-8")).decode("ascii")}
    code, existing = http("GET", API + "/repos/{}/contents/{}?ref={}".format(head_repo, path, branch), token, None)
    if code == 200 and isinstance(existing, dict) and existing.get("sha"):
        put.update(sha=existing["sha"], message="catalog: update {}".format(name))
    _call(http, token, "writing " + path, "PUT", "/repos/{}/contents/{}".format(head_repo, path), put)
    pr = _call(http, token, "opening the catalog pull request", "POST", "/repos/{}/pulls".format(upstream), {
        "title": put["message"], "base": base, "maintainer_can_modify": True,
        "head": branch if head_repo == upstream else "{}:{}".format(head_repo.split("/", 1)[0], branch),
        "body": "Adds the catalog entry for the rodeo `{}`, published from the Rodeo Builder.\n\n```yaml\n{}```\n".format(name, entry)})
    return str(pr.get("html_url") or "")


def publish(req: Any, http: Http = _http, run: Run = subprocess.run,
            sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Publish ``{"name", "title", "engine", "languages", "files": [{"path", "content", "executable"}],
    "update"}``; returns ``{"repo", "pull_request", "login", "catalog_error"}`` (*catalog_error*: why
    the catalog pull request could not be opened; the repository is published). ValueError for a bad request,
    PermissionError without a GitHub login, PublishError when GitHub or git fails."""
    if not isinstance(req, dict):
        raise ValueError("send a JSON object")
    name = check_name(str(req.get("name") or ""))
    title = str(req.get("title") or name).strip()[:200]
    engine = str(req.get("engine") or "")
    languages = [lang for lang in req.get("languages") or [] if isinstance(lang, str) and _LANG_RE.match(lang)] or ["en"]
    try:
        files = [ProfileFile(str(f["path"]), str(f["content"]).encode("utf-8"), bool(f.get("executable")))
                 for f in req.get("files") or []]
    except (KeyError, TypeError) as exc:
        raise ValueError("bad file list: {}".format(exc)) from exc
    check_files(files)
    token, source = credentials(run)
    if not token:
        raise PermissionError("log in to GitHub first")
    me = _call(http, token, "reading your GitHub account (login from {})".format(source), "GET", "/user")
    login = str(me["login"])
    author = (str(me.get("name") or login), "{}+{}@users.noreply.github.com".format(me.get("id"), login))
    update = bool(req.get("update"))
    if not update:
        code, body = http("POST", API + "/user/repos", token, {"name": name, "description": title, "private": False,
                                                                "has_wiki": False, "auto_init": False})
        if code == 422 and "exist" in _message(body).lower():
            raise FileExistsError("github.com/{}/{} already exists".format(login, name))
        if code not in (200, 201):
            raise PublishError("creating github.com/{}/{} (GitHub {}): {}".format(login, name, code, _message(body)))
    repo = "https://{}/{}/{}".format(HOST, login, name)
    _push(run, files, repo + ".git", author, token, ("Update" if update else "Publish") + " the rodeo " + name, update)
    pull, catalog_error = "", ""
    if not update:
        try:
            pull = _open_catalog_pr(http, token, login, name, catalog_entry(name, title, repo, engine, languages), sleep)
        except PublishError as exc:
            catalog_error = str(exc)
    return {"repo": repo, "pull_request": pull, "login": login, "catalog_error": catalog_error}
