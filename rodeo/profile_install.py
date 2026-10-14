"""Install a rodeo composed in the Rodeo Builder as a custom profile.

A builder rodeo is a set of files relative to the profile directory (the same
set the builder's zip download holds) plus, optionally, the bundled or custom
profile it was built on. :func:`install_profile` copies that base profile, lays
the files over it and writes the result to ``~/.rodeo/profiles/<name>/``.
``rodeo new --from-zip`` and the ``rodeo builder`` server's Save both use it.

The files come from a browser or a downloaded zip, so they are checked before
anything is written: only the paths a builder rodeo has (plan, definition,
README, lab.json, the builder manifest, ``story/`` and ``checks/``), no hidden
or parent segments, bounded sizes, and executable bits only on ``checks/``.
"""
from __future__ import annotations

import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from .labseed import custom_profile_dir, normalize_plan, resolve_profile_source

MANIFEST = "builder.yaml"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_TOP_FILES = {"rodeo-plan.yaml", "definition.yaml", "README.md", "lab.json", MANIFEST}
_TOP_DIRS = {"story", "checks"}
MAX_FILES = 500
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
_TARGETS = ("baremetal", "instruqt", "aws")


@dataclass(frozen=True)
class ProfileFile:
    path: str
    data: bytes
    executable: bool = False


def check_name(name: str) -> str:
    """The profile name, or ValueError when it is not a lowercase slug."""
    if not NAME_RE.match(name or ""):
        raise ValueError(f"profile name {name!r}: use lowercase letters, digits and '-' (max 63)")
    return name


def check_path(path: str) -> str:
    """A safe profile-relative path from the builder's set, or ValueError."""
    parts = path.split("/")
    ok = (
        all(_SEGMENT_RE.match(p) for p in parts)
        and len(parts) <= 4
        and ((len(parts) == 1 and parts[0] in _TOP_FILES) or (len(parts) > 1 and parts[0] in _TOP_DIRS))
    )
    if not ok:
        raise ValueError(f"{path!r} is not a file a builder rodeo can hold")
    return path


def check_files(files: list[ProfileFile]) -> None:
    """Paths, sizes and modes of a builder rodeo; ValueError on the first problem."""
    if not files:
        raise ValueError("the rodeo has no files")
    if len(files) > MAX_FILES:
        raise ValueError(f"too many files ({len(files)} > {MAX_FILES})")
    if not any(f.path == "rodeo-plan.yaml" for f in files):
        raise ValueError("the rodeo has no rodeo-plan.yaml")
    seen: set[str] = set()
    total = 0
    for f in files:
        check_path(f.path)
        if f.path in seen:
            raise ValueError(f"{f.path} appears twice")
        seen.add(f.path)
        if len(f.data) > MAX_FILE_BYTES:
            raise ValueError(f"{f.path} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB")
        if f.executable and not f.path.startswith("checks/"):
            raise ValueError(f"{f.path}: only checks/ scripts may be executable")
        total += len(f.data)
    if total > MAX_TOTAL_BYTES:
        raise ValueError(f"the rodeo is larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MiB")


def manifest_base(files: list[ProfileFile]) -> str | None:
    """The base profile named in the builder manifest, if any."""
    for f in files:
        if f.path == MANIFEST:
            data = yaml.safe_load(f.data.decode("utf-8")) or {}
            base = data.get("base") if isinstance(data, dict) else None
            return str(base) if base else None
    return None


def install_profile(name: str, files: list[ProfileFile], base: str | None = None, force: bool = False) -> Path:
    """Write a builder rodeo to ~/.rodeo/profiles/<name>/ and return that directory.

    With *base*, the base profile is copied first and *files* replace or add to
    it. The plan keeps its own name, deployment_target and comments when they
    are already beginner-safe; otherwise it is normalized like ``rodeo new``
    does. Raises FileExistsError when the profile exists and *force* is not
    set, ValueError for a bad name or file set, FileNotFoundError for an
    unknown base. Nothing is written unless every check passes.
    """
    check_name(name)
    check_files(files)
    src = resolve_profile_source(base) if base else None
    dest = custom_profile_dir(name)
    if dest.exists() and not force:
        raise FileExistsError(f"Profile '{name}' already exists at {dest} (use --force to overwrite)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{name}.", dir=dest.parent))
    try:
        stage = work / name
        if src:
            shutil.copytree(src, stage)
        else:
            stage.mkdir()
        for f in files:
            target = stage / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.is_dir():
                raise ValueError(f"{f.path} would replace a {'link' if target.is_symlink() else 'directory'} of the base")
            target.write_bytes(f.data)
            target.chmod(0o755 if f.executable else 0o644)
        _normalize(stage / "rodeo-plan.yaml", name)
        if dest.exists():
            shutil.rmtree(dest)
        stage.rename(dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return dest


def _normalize(plan: Path, name: str) -> None:
    data = yaml.safe_load(plan.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("rodeo-plan.yaml is not a mapping")
    target = data.get("deployment_target") or "baremetal"
    if target not in _TARGETS:
        raise ValueError(f"rodeo-plan.yaml: unknown deployment_target {target!r} (use {', '.join(_TARGETS)})")
    creds = data.get("credentials")
    env_creds = isinstance(creds, dict) and any(isinstance(v, str) and v.startswith("??env:") for v in creds.values())
    device = isinstance(data.get("storage"), dict) and data["storage"].get("device")
    if data.get("name") != name or env_creds or device or target == "instruqt":
        normalize_plan(plan, name=name, deployment_target=target)


def read_zip(path: Path) -> list[ProfileFile]:
    """The files of a builder zip, paths relative to its single top directory.

    Refuses links, entries outside a single top directory and oversize content
    (checked from the zip's own sizes before reading).
    """
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos:
            raise ValueError(f"{path} holds no files")
        if len(infos) > MAX_FILES:
            raise ValueError(f"{path}: too many files ({len(infos)} > {MAX_FILES})")
        tops = {i.filename.split("/", 1)[0] for i in infos}
        strip = len(tops) == 1 and all("/" in i.filename for i in infos)
        total = 0
        out = []
        for info in infos:
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"{info.filename} is a link")
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError(f"{info.filename} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ValueError(f"{path} is larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MiB")
            rel = info.filename.split("/", 1)[1] if strip else info.filename
            check_path(rel)
            out.append(ProfileFile(rel, zf.read(info), bool(mode & 0o111)))
        return out
