"""Writes rodeo makes inside a lab directory, without following planted symlinks.

The paths rodeo writes (content.lock, guide/, .labinabox/, workshop-guide/,
.rodeo-secrets.yaml...) are left out of the lab hash, so a symlink planted there
after `rodeo trust` would otherwise redirect a root write anywhere.

Directories are walked with ``os.open(O_DIRECTORY | O_NOFOLLOW)`` one component
at a time, and files are created and renamed relative to the final directory's
file descriptor (``dir_fd``), so a component swapped for a symlink between the
check and the write is not followed. Linux and macOS support all of this.
Residual race: ``lab_subdir`` and ``clear_lab_path`` hand back a path, and code
that then writes many files under it by path (rendered guides, the PDF build
copy) is only protected up to that moment. ``clear_lab_path`` removes a
directory with ``shutil.rmtree(name, dir_fd=...)`` on Python 3.11+; on older
Python it falls back to the path, where a parent swapped for a symlink after
the check could redirect the removal.
"""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from .config import ConfigError

_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_dir(lab: Path, rel: Path, mode: int = 0o700) -> int:
    """An fd for ``lab/rel``, created as needed, never through a symlink."""
    for part in rel.parts:
        if part in ("", ".", "..") or "/" in part:
            raise ConfigError(f"{rel}: not a plain path inside the lab")
    try:
        fd = os.open(os.path.realpath(lab), _DIR_FLAGS)
    except OSError as exc:
        raise ConfigError(f"{lab}: cannot open the lab directory: {exc}")
    try:
        for part in rel.parts:
            try:
                nfd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, mode, dir_fd=fd)
                nfd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                raise ConfigError(
                    f"{lab / rel}: {part} is a symlink or not a directory ({exc.strerror}): "
                    "rodeo does not write through links in a lab"
                )
            os.close(fd)
            fd = nfd
    except BaseException:
        os.close(fd)
        raise
    return fd


def lab_subdir(lab: Path, rel: str | Path, mode: int = 0o700) -> Path:
    """``lab/rel`` as a real directory (created if missing); refuses a symlink
    anywhere between the lab and it."""
    os.close(_open_dir(Path(lab), Path(rel), mode))
    return Path(os.path.realpath(lab)) / rel


def write_lab_file(lab: Path, rel: str | Path, data: str | bytes, mode: int = 0o600) -> Path:
    """Atomically write ``lab/rel``: a temp file and a rename, both relative to the
    checked directory's fd. A symlink at ``rel`` is replaced, never followed.

    Replacing an existing regular file keeps its owner (so `sudo rodeo up` does
    not hand the user's plan to root) and never loosens its mode: the result is
    the existing mode limited to ``mode``. New directories on the way are 0700.
    """
    rel = Path(rel)
    dfd = _open_dir(Path(lab), rel.parent)
    tmp = f".{rel.name}.{secrets.token_hex(6)}"
    try:
        owner = None
        try:
            st = os.stat(rel.name, dir_fd=dfd, follow_symlinks=False)
            if stat.S_ISREG(st.st_mode):
                owner = (st.st_uid, st.st_gid)
                mode = (st.st_mode & 0o777) & mode
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                     mode, dir_fd=dfd)
        try:
            try:
                fh = os.fdopen(fd, "wb")
            except BaseException:
                os.close(fd)
                raise
            with fh:
                os.fchmod(fh.fileno(), mode)
                if owner is not None and owner != (os.geteuid(), os.getegid()):
                    try:
                        os.fchown(fh.fileno(), *owner)
                    except PermissionError:
                        pass  # not root: the file stays ours, as before
                fh.write(data.encode() if isinstance(data, str) else data)
            os.replace(tmp, rel.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)
    return Path(os.path.realpath(lab)) / rel


def read_lab_file(lab: Path, rel: str | Path) -> str | None:
    """Text of ``lab/rel``, or None if missing; refuses a symlink or a hard link."""
    rel = Path(rel)
    dfd = _open_dir(Path(lab), rel.parent)
    try:
        try:
            fd = os.open(rel.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dfd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ConfigError(f"{lab / rel} is a symlink ({exc.strerror}): refusing it")
        with os.fdopen(fd, "r") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise ConfigError(f"{lab / rel} is a hard link or not a regular file: refusing it")
            return fh.read()
    finally:
        os.close(dfd)


def clear_lab_path(lab: Path, rel: str | Path) -> Path:
    """Remove ``lab/rel`` before rodeo rebuilds it: a symlink is unlinked, never followed."""
    import shutil
    import sys

    rel = Path(rel)
    dfd = _open_dir(Path(lab), rel.parent)
    path = Path(os.path.realpath(lab)) / rel
    try:
        try:
            st = os.stat(rel.name, dir_fd=dfd, follow_symlinks=False)
        except FileNotFoundError:
            return path
        if stat.S_ISDIR(st.st_mode):
            if sys.version_info >= (3, 11) and shutil.rmtree.avoids_symlink_attacks:
                shutil.rmtree(rel.name, dir_fd=dfd)  # relative to the checked parent
            else:
                # Older Python: by path. A parent swapped for a link after the
                # check could redirect this rmtree (documented residual race).
                shutil.rmtree(path)
        else:
            os.unlink(rel.name, dir_fd=dfd)
    finally:
        os.close(dfd)
    return path
