"""`rodeo fleet player-setup`: install the rodeo player on every lab host.

Runs ``rodeo player setup`` on each host over the fleet's SSH (as root, like
every fleet step). The access links never come back to the terminal: they
stay root-only on each host and reach students through
``rodeo fleet portal publish`` with ``portal.player: true``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..config import ConfigError
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


def setup_argv(inventory: FleetInventory, host: FleetHost, track_ref: str) -> list[str]:
    argv = ["rodeo", "player", "setup", "--track-ref", track_ref, "--json"]
    if inventory.player is not None:
        pid = player_id(host)
        if not pid:
            raise ConfigError(f"{host.id}: no labels.player_id (run `rodeo fleet dns` first)")
        argv += ["--domain", inventory.player.domain, "--label", pid, "--tls-stdin"]
    return argv


def _one(inventory: FleetInventory, host: FleetHost, track_ref: str, tls_pem: str | None) -> PlayerSetupRow:
    try:
        argv = setup_argv(inventory, host, track_ref)
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


def fleet_player_setup(inventory: FleetInventory, *, track_ref: str, concurrency: int = 8) -> list[PlayerSetupRow]:
    if not _SHA_RE.match(track_ref):
        raise ConfigError("--track-ref must be a full 40-character commit SHA (pin the track)")
    if not inventory.hosts:
        raise ConfigError("no hosts in the inventory (run `rodeo fleet provision` first)")
    tls_pem = None
    if inventory.player is not None:
        from .cert import bundle_for_push

        tls_pem = bundle_for_push(inventory.player.domain, inventory.player.staging)
    return fanout(inventory.hosts, lambda h: _one(inventory, h, track_ref, tls_pem), concurrency=concurrency)
