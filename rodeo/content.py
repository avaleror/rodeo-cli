"""A lab's instructions, kept apart from its infrastructure.

A plan's optional ``content:`` block names where the instructions live:

    content:
      repo: https://github.com/<owner>/<repo>
      ref: main             # branch, tag or commit; default: the default branch
      path: story           # directory in the repository; default: the root
      format: rmstory       # rmstory | instruqt | markdown
      facts:                # instruqt: values for [[ Instruqt-Var key="..." ]]
        SMLM_URL: https://smlm.rodeo.lab
      replace:              # literal text swaps when rendering
        "example.com": rodeo.lab

:func:`apply` fetches the content into ``<lab>/content/`` (``<lab>/content.lock``
records the commit) and renders the guide into ``<lab>/guide/`` without touching
the deployed environment, so the instructions can be updated on a running lab.
An rmstory content is also what ``rodeo story render`` reads when the lab has no
``story/`` of its own. The plan's ``workshop:`` block and an in-profile ``story/``
work as before.

A content repository has the profile layout at its root, whatever ``path`` names:

- ``checks/`` is installed by :func:`install_checks` into
  ``/opt/rodeo/<plan name>/checks/`` (root-owned, readable and runnable by every
  user on the host); ``rodeo clean`` removes it (:func:`remove_checks`);
- ``custom/scripts/`` holds the content's setup scripts: :func:`run_setup` runs the
  executable ones in sorted order on the host, from that directory, with the
  environment the profile's own custom scripts get plus ``RODEO_CONTENT_DIR`` (the
  fetched repository). They should be idempotent: they run on every apply.
  ``rodeo up`` leaves out the ones named like a script of the lab's own
  ``custom/scripts/`` (:func:`profile_script_names`), which already ran.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Collection, Iterator

import yaml

from .config import ConfigError
from .labinabox_host import fetch_commands
from .workshop import render_text

FORMATS = ("rmstory", "instruqt", "markdown")
CHECKOUT = "content"
CHECKS_ROOT = Path("/opt/rodeo")
SETUP_TIMEOUT = 1800
_PLAN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
LOCK = "content.lock"
GUIDE = "guide"
TIMEOUT = 300
_REF_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,199}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._ -]{0,127}$")

Run = Callable[..., "subprocess.CompletedProcess[str]"]


def content_spec(cfg: dict) -> dict[str, Any] | None:
    """Validated ``content:`` block, or None when the plan has none."""
    spec = cfg.get("content")
    if not spec:
        return None
    if not isinstance(spec, dict):
        raise ConfigError("content: must be a mapping with repo, format and optionally ref and path")
    repo, ref = str(spec.get("repo") or ""), str(spec.get("ref") or "")
    path, fmt = str(spec.get("path") or "").strip("/"), str(spec.get("format") or "")
    if not repo.startswith("https://") or "@" in repo.split("://", 1)[1].split("/", 1)[0]:
        raise ConfigError("content.repo must be an https:// git URL without credentials")
    if ref and not _REF_RE.match(ref):
        raise ConfigError(f"content.ref '{ref}' is not a branch, tag or commit")
    if path and not all(_SEGMENT_RE.match(seg) for seg in path.split("/")):
        raise ConfigError(f"content.path '{path}' is not a plain path in the repository")
    if fmt not in FORMATS:
        raise ConfigError(f"content.format must be one of {', '.join(FORMATS)}")
    return {"repo": repo, "ref": ref, "path": path, "format": fmt,
            "facts": {str(k): str(v) for k, v in (spec.get("facts") or {}).items()},
            "replace": {str(k): str(v) for k, v in (spec.get("replace") or {}).items()}}


def content_root(lab: Path, spec: dict[str, Any]) -> Path:
    """The fetched content's directory (``<lab>/content/<path>``)."""
    return lab / CHECKOUT / spec["path"] if spec["path"] else lab / CHECKOUT


def fetch(spec: dict[str, Any], lab: Path, run: Run = subprocess.run) -> str:
    """Fetch the content into ``<lab>/content/`` (replacing what was there); returns the commit."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true"}
    lab.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".content.", dir=lab))
    try:
        dest = work / CHECKOUT
        cmds = fetch_commands(spec["repo"], spec["ref"] or "HEAD", dest)
        # A symlink in the repository is checked out as a plain file holding its target,
        # so rendering can't copy a file of this machine into the guide.
        cmds.insert(1, ["git", "-C", str(dest), "config", "core.symlinks", "false"])
        for cmd in cmds:
            try:
                r = run(cmd, capture_output=True, text=True, env=env, timeout=TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"fetching {spec['repo']} took longer than {TIMEOUT} s") from exc
            if r.returncode != 0:
                raise RuntimeError(f"fetching {spec['repo']}: {(r.stderr or r.stdout).strip()[-300:]}")
        r = run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, env=env, timeout=TIMEOUT)
        commit = r.stdout.strip() if r.returncode == 0 else ""
        shutil.rmtree(dest / ".git", ignore_errors=True)
        if not content_root(work, spec).is_dir():
            raise ConfigError(f"{spec['repo']} has no directory '{spec['path']}'")
        target = lab / CHECKOUT
        if target.exists():
            shutil.rmtree(target)
        dest.rename(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    (lab / LOCK).write_text(yaml.safe_dump({"repo": spec["repo"], "ref": spec["ref"] or None, "path": spec["path"],
                                            "format": spec["format"], "commit": commit}, sort_keys=False))
    return commit


def _write_private(dest: Path, text: str) -> Path:
    dest.touch(mode=0o600, exist_ok=True)
    dest.chmod(0o600)
    dest.write_text(text)
    return dest


def _replace(text: str, spec: dict[str, Any]) -> str:
    for old, new in spec["replace"].items():
        text = text.replace(old, new)
    return text


def render(spec: dict[str, Any], lab: Path, cfg: dict) -> list[Path]:
    """Write the guide into ``<lab>/guide/`` (owner-only: facts can hold lab passwords)."""
    root = content_root(lab, spec)
    out = lab / GUIDE
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(mode=0o700, parents=True)
    written: list[Path] = []
    if spec["format"] == "instruqt":
        for challenge in sorted(p for p in root.iterdir() if (p / "assignment.md").is_file()):
            text = render_text((challenge / "assignment.md").read_text(errors="replace"), spec)
            written.append(_write_private(out / f"{challenge.name}.md", text))
    elif spec["format"] == "markdown":
        for source in sorted(root.glob("*.md")):
            written.append(_write_private(out / source.name, _replace(source.read_text(errors="replace"), spec)))
    else:
        from .story import render_story

        text = render_story(cfg, root=root)
        written.append(_write_private(out / "guide.md", _replace(text, spec)))
    if not written:
        raise ConfigError(f"no {spec['format']} chapters found in {root}")
    return written


def checks_dir(cfg: dict) -> Path:
    """Where the content's checks are installed: ``/opt/rodeo/<plan name>/checks``."""
    name = str(cfg.get("name") or "")
    if not _PLAN_NAME_RE.match(name):
        raise ConfigError(f"plan name '{name}' can't name a directory under {CHECKS_ROOT}")
    return CHECKS_ROOT / name / "checks"


def install_checks(cfg: dict, lab: Path) -> Path | None:
    """Copy the fetched content's ``checks/`` to :func:`checks_dir`, replacing what was there.

    Directories get mode 755, files 755 when executable in the repository and 644
    otherwise; anything but plain files and directories is skipped. Returns the
    directory, or None when the content has no ``checks/``. Raises PermissionError
    when not running as root.
    """
    src = lab / CHECKOUT / "checks"
    if not src.is_dir():
        return None
    dest = checks_dir(cfg)
    if os.geteuid() != 0:
        raise PermissionError(f"installing the checks into {dest} needs root: sudo rodeo content apply")
    dest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".checks.", dir=dest.parent))
    try:
        for item in sorted(src.rglob("*")):
            target = stage / item.relative_to(src)
            if item.is_symlink():
                continue
            if item.is_dir():
                target.mkdir(mode=0o755, exist_ok=True)
            elif item.is_file():
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                shutil.copyfile(item, target)
                target.chmod(0o755 if os.access(item, os.X_OK) else 0o644)
        stage.chmod(0o755)
        if dest.exists():
            shutil.rmtree(dest)
        stage.rename(dest)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return dest


def remove_checks(cfg: dict) -> Path | None:
    """Remove :func:`checks_dir`, and its plan directory when that is left empty.

    Returns the removed directory, or None when there was none (or the plan name
    can't name one).
    """
    try:
        dest = checks_dir(cfg)
    except ConfigError:
        return None
    return _remove_checks(dest)


def remove_all_checks() -> list[Path]:
    """Remove the checks of every plan under CHECKS_ROOT; returns the removed directories."""
    if not CHECKS_ROOT.is_dir():
        return []
    removed = (_remove_checks(plan / "checks") for plan in sorted(CHECKS_ROOT.iterdir())
               if plan.is_dir() and not plan.is_symlink())
    return [path for path in removed if path]


def _remove_checks(dest: Path) -> Path | None:
    if dest.is_symlink() or not dest.is_dir():
        return None
    shutil.rmtree(dest)
    try:
        dest.parent.rmdir()
    except OSError:
        pass
    return dest


def setup_scripts(lab: Path) -> list[Path]:
    """The fetched content's executable ``custom/scripts/*``, in the order they run."""
    scripts = lab / CHECKOUT / "custom" / "scripts"
    if not scripts.is_dir():
        return []
    return sorted(f for f in scripts.iterdir() if f.is_file() and not f.is_symlink() and os.access(f, os.X_OK))


def setup_guarded(cfg: dict) -> bool:
    """True when ``rodeo up`` leaves the setup scripts out: on ``deployment_target: instruqt``
    with a profile that guards its own custom scripts (they belong in the baked image)."""
    from .profiles import get_profile

    if cfg.get("deployment_target", "baremetal") != "instruqt":
        return False
    return "custom_scripts" in get_profile(str(cfg.get("type") or "suse-virt")).guarded_phases


def profile_script_names(cfg: dict) -> set[str]:
    """Names of the lab's own ``custom/scripts/*`` that the profile's ``custom_scripts`` phase runs."""
    from .profiles import get_profile

    if "custom_scripts" not in get_profile(str(cfg.get("type") or "suse-virt")).phases or not cfg.get("config_dir"):
        return set()
    scripts = Path(cfg["config_dir"]) / "custom" / "scripts"
    if not scripts.is_dir():
        return set()
    return {f.name for f in scripts.iterdir() if f.is_file() and os.access(f, os.X_OK)}


def run_setup(cfg: dict, lab: Path, run: Run = subprocess.run,
              skip: Collection[str] = ()) -> Iterator[tuple[str, int, list[str]]]:
    """Run :func:`setup_scripts` but those named in *skip*; yields ``(script name, exit code,
    output lines)`` for each.

    Every script runs even when an earlier one failed; a script that times out
    after SETUP_TIMEOUT seconds or can't start yields exit code -1.
    """
    from .config import find_ansible_root
    from .paths import harvester_kubeconfig_path

    scripts = [s for s in setup_scripts(lab) if s.name not in skip]
    if not scripts:
        return
    env = {**os.environ, "RODEO_PLAN_NAME": str(cfg.get("name", "default")),
           "RODEO_CONFIG_DIR": str(cfg.get("config_dir", "")), "RODEO_CONTENT_DIR": str(lab / CHECKOUT)}
    root = find_ansible_root(cfg)
    if root is not None:
        env["RODEO_LAB_DIR"] = str(root)
    if harvester_kubeconfig_path().exists():
        env["KUBECONFIG"] = str(harvester_kubeconfig_path())
    for script in scripts:
        try:
            r = run([str(script)], cwd=str(script.parent), env=env, capture_output=True, text=True,
                    timeout=SETUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            yield script.name, -1, [f"timed out after {SETUP_TIMEOUT} s"]
            continue
        except OSError as exc:
            yield script.name, -1, [str(exc)]
            continue
        yield script.name, r.returncode, [line for line in (r.stdout + r.stderr).splitlines() if line.strip()]


def apply(cfg: dict, lab: Path, run: Run = subprocess.run) -> dict[str, Any]:
    """Fetch the plan's content, render the guide and install its checks.

    Returns ``{"commit", "files", "checks", "warnings"}``: ``checks`` is the
    installed directory or None, ``warnings`` says why the checks weren't
    installed. The setup scripts are :func:`run_setup`'s. Raises ConfigError when
    the plan has no ``content:`` block.
    """
    spec = content_spec(cfg)
    if spec is None:
        raise ConfigError("this plan has no content: block (see docs/reference/plan.md#content)")
    commit = fetch(spec, lab, run)
    files = render(spec, lab, cfg)
    warnings: list[str] = []
    try:
        checks = install_checks(cfg, lab)
    except (PermissionError, ConfigError) as exc:
        checks, warnings = None, [str(exc)]
    return {"commit": commit, "files": files, "checks": checks, "warnings": warnings}

