"""rodeo logins — the users who may log in to the Rodeo Builder.

    rodeo logins add alice          # asks for the password twice
    rodeo logins list
    rodeo logins remove alice

A login is needed to add or delete stored credentials, and for everything on an
exposed builder that acts as its user (deploying, stored credentials). The file is in
htpasswd format with bcrypt hashes (rodeo/builder/login.py): /etc/rodeo-builder/htpasswd
when it exists (a KVM host, shared with lab-builder), else ~/.rodeo/builder/htpasswd;
--file names another one. A user with an APR1-MD5 hash (htpasswd -m) can log in and
moves to bcrypt when its password is set again.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Callable, TypeVar

import click

from ..builder import login

T = TypeVar("T")
WAIT_AFTER = 1.0
KIND_NOTE = {"apr1-md5": "  (APR1-MD5: set a new password to move it to bcrypt)",
             "other": "  (a hash rodeo cannot check: set a new password)"}
FILE_OPTION = click.option("--file", "path", type=click.Path(dir_okay=False, path_type=Path),
                           help="Login file to use instead of the one rodeo builder reads.")


def with_waiting_note(work: Callable[[], T], note: str = "waiting (hashing the password)...") -> T:
    """Runs *work*, printing *note* to stderr once when it takes longer than :data:`WAIT_AFTER` seconds."""
    done = threading.Event()
    result: list[T] = []
    error: list[BaseException] = []

    def run() -> None:
        try:
            result.append(work())
        except BaseException as exc:  # noqa: BLE001 - re-raised in the calling thread
            error.append(exc)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    if not done.wait(WAIT_AFTER):
        click.echo(note, err=True)
        done.wait()
    if error:
        raise error[0]
    return result[0]


@click.group("logins")
def logins_cmd() -> None:
    """Users who may log in to the Rodeo Builder (bcrypt, htpasswd format)."""


@logins_cmd.command("add")
@click.argument("user")
@click.option("--password-stdin", is_flag=True, help="Read the password from standard input instead of asking.")
@FILE_OPTION
def add(user: str, password_stdin: bool, path: Path | None) -> None:
    """Add USER, or set a new password for it (an APR1-MD5 user becomes bcrypt)."""
    if password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = click.prompt("Password for {}".format(user), hide_input=True, confirmation_prompt=True)
    try:
        written = with_waiting_note(lambda: login.set_user(user, password, path))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    except OSError as exc:
        raise click.ClickException("could not write {}: {}".format(path or login.login_file(), exc)) from exc
    click.echo("Added {} to {}".format(user, written))


@logins_cmd.command("remove")
@click.argument("user")
@FILE_OPTION
def remove(user: str, path: Path | None) -> None:
    """Remove USER."""
    try:
        gone = login.remove_user(user, path)
    except OSError as exc:
        raise click.ClickException("could not write {}: {}".format(path or login.login_file(), exc)) from exc
    if not gone:
        raise click.ClickException("{} is not in {}".format(user, path or login.login_file()))
    click.echo("Removed {} from {}".format(user, path or login.login_file()))


@logins_cmd.command("list")
@FILE_OPTION
def list_(path: Path | None) -> None:
    """List the users and the kind of hash each one has."""
    where = path or login.login_file()
    users = login.list_users(path)
    click.echo(str(where) + ("" if users else " (no users yet: rodeo logins add <user>)"))
    for user, kind in users:
        click.echo("  " + user + KIND_NOTE.get(kind, ""))
