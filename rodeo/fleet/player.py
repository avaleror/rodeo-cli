"""`rodeo fleet player-setup`: install the rodeo player on every lab host.

Runs ``rodeo player setup`` on each host over the fleet's SSH (as root, like
every fleet step). The access links never come back to the terminal: they
stay root-only on each host and reach students through
``rodeo fleet portal publish``.

What the player shows comes from the lab profile's ``player.yaml``. A profile
with a track needs ``--track-ref`` (the commit to pin); one without a track
(instructions on a web guide, or none) needs nothing more.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..config import ConfigError
from ..player.runner import check_timeout_ok
from .fanout import fanout
from .inventory import FleetHost, FleetInventory, player_id
from .ssh_exec import run_remote

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SETUP_TIMEOUT = 1200.0


@dataclass(frozen=True)
class PlayerSetupRow:
    id: str
    ok: bool
    player: str = ""        # https://<name>/ (no token)
    chapters: int = 0
    error: str = ""         # last lines of stderr, never stdout (it holds the link)


def player_profile(inventory: FleetInventory):  # -> PlayerProfile
    """The lab profile's player.yaml, or a ConfigError that says what to do."""
    from ..player.profile import ProfileError, has_profile, load

    name = inventory.lab_profile or ""
    if not has_profile(name):
        raise ConfigError(
            f"lab profile {name or '(none)'!r} has no player.yaml, so the student UI does not know "
            "what to show. Add rodeo/data/examples/<profile>/player.yaml, or set `player: false` "
            "in workshop.yaml for a fleet without the student UI.")
    try:
        return load(name)
    except ProfileError as exc:
        raise ConfigError(str(exc)) from exc


def setup_argv(inventory: FleetInventory, host: FleetHost, track_ref: str | None,
               check_timeout: int | None = None) -> list[str]:
    argv = ["rodeo", "player", "setup", "--json"]
    if track_ref:
        argv += ["--track-ref", track_ref]
    from ..player.profile import DEFAULT_PROFILE, has_profile

    # Only a non-default profile is named, so a host on an older rodeo keeps
    # working for virt-rodeo.
    if has_profile(inventory.lab_profile) and inventory.lab_profile != DEFAULT_PROFILE:
        argv += ["--profile", str(inventory.lab_profile)]
    if check_timeout is not None:
        argv += ["--check-timeout", str(check_timeout_ok(check_timeout))]
    if inventory.player is not None:
        pid = player_id(host)
        if not pid:
            raise ConfigError(f"{host.id}: no labels.player_id (run `rodeo fleet dns` first)")
        argv += ["--domain", inventory.player.domain, "--label", pid, "--tls-stdin"]
    return argv


def _one(inventory: FleetInventory, host: FleetHost, track_ref: str | None, tls_pem: str | None,
         check_timeout: int | None = None) -> PlayerSetupRow:
    try:
        argv = setup_argv(inventory, host, track_ref, check_timeout)
    except ConfigError as exc:
        return PlayerSetupRow(host.id, False, error=str(exc))
    # The certificate key goes over the SSH session's stdin, never argv or a file.
    res = run_remote(inventory, host, argv, timeout=SETUP_TIMEOUT, stdin=tls_pem)
    if not res.ok:
        return PlayerSetupRow(host.id, False, error=(res.stderr or "").strip()[-300:] or f"exit {res.rc}")
    try:
        out = json.loads(res.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return PlayerSetupRow(host.id, False, error="setup printed no result")
    return PlayerSetupRow(host.id, True, player=str(out.get("player", "")), chapters=int(out.get("chapters", 0)))


def require_track_ref(inventory: FleetInventory, track_ref: str | None) -> str | None:
    """The pin a track needs, checked; None for a profile without a track."""
    prof = player_profile(inventory)
    if not prof.track_repo:
        return None
    if not (track_ref and _SHA_RE.match(track_ref)):
        raise ConfigError(f"lab profile {prof.name} has a track: give --track-ref <commit SHA, "
                          "40 hex> to pin it")
    return track_ref


def fleet_player_setup(inventory: FleetInventory, *, track_ref: str | None = None, concurrency: int = 8,
                       check_timeout: int | None = None) -> list[PlayerSetupRow]:
    track_ref = require_track_ref(inventory, track_ref)
    if check_timeout is not None:
        try:
            check_timeout_ok(check_timeout)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    if not inventory.hosts:
        raise ConfigError("no hosts in the inventory (run `rodeo fleet provision` first)")
    tls_pem = None
    if inventory.player is not None:
        from .cert import bundle_for_push

        tls_pem = bundle_for_push(inventory.player.domain, inventory.player.staging)
    return fanout(inventory.hosts, lambda h: _one(inventory, h, track_ref, tls_pem, check_timeout),
                  concurrency=concurrency)
