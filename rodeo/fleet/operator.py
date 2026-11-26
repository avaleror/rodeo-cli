"""`rodeo fleet operator`: run fleet commands from a small host in the cloud.

A long ``fleet up`` should not depend on the laptop: a lid closed, a sleep or
a dropped Wi-Fi stops it. The operator host is a small instance in the
workshop's subnet that runs the fleet command for you::

    rodeo fleet operator up    -f workshop.yaml   # create it, install rodeo + AWS CLI
    rodeo fleet operator login -f workshop.yaml   # `aws login --remote` on it, once
    rodeo fleet operator push  -f workshop.yaml   # workshop dir, lab key, certs -> host
    rodeo fleet operator run   -f workshop.yaml -- up --track-ref <sha> --images-bucket <b>
    rodeo fleet operator logs  -f workshop.yaml   # follow it (Ctrl-C only stops following)
    rodeo fleet operator pull  -f workshop.yaml   # results back to the laptop
    rodeo fleet operator down  -f workshop.yaml

Credentials: the host has no instance role (that needs IAM rights a
PowerUser does not have). ``login`` runs ``aws login --remote`` on it: you
open the URL on any device and paste the code back. The CLI renews the
session on its own for as long as the console session lasts. An instance
role would later replace this with no code change: boto3 uses the default
credential chain either way.

What lives on the host: the lab's SSH private key, the event certificate and
an AWS session. So its security group opens SSH to this machine's /32 only,
it carries the dead-man switch, and ``down`` removes it.
"""
from __future__ import annotations

import io
import re
import shlex
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

import yaml

from ..config import ConfigError
from .inventory import FleetHost, FleetInventory, require_provider
from .ssh_exec import known_hosts_path, run_remote, ssh_argv

HOST_ID = "operator"
REMOTE_WORKSHOPS = "rodeo-operator"     # under root's home on the operator host
UNIT = "rodeo-operator-run"
AWS = "/usr/local/bin/aws"               # the v2 CLI install_cli puts there
ALSO_ALLOW_ENV = "RODEO_ALSO_ALLOW_IP"   # same name as providers.aws.ALSO_ALLOW_ENV
MAX_PUSH_BYTES = 200 * 1024 * 1024      # the workshop dir is YAML, keys and logs
_PULL_SKIP = {".DS_Store"}


@dataclass(frozen=True)
class OperatorHost:
    ssh: str
    public_ip: str
    provider_id: str
    ssh_user: str

    def as_fleet_host(self) -> FleetHost:
        return FleetHost(id=HOST_ID, ssh=self.ssh, public_ip=self.public_ip, ssh_user=self.ssh_user)


# ---------------------------------------------------------------- local state

def state_path(inventory: FleetInventory) -> Path:
    return known_hosts_path(inventory.name).parent / "operator.yaml"


def load_state(inventory: FleetInventory) -> OperatorHost | None:
    path = state_path(inventory)
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text()) or {}
    if not raw.get("ssh"):
        return None
    return OperatorHost(ssh=str(raw["ssh"]), public_ip=str(raw.get("public_ip") or ""),
                        provider_id=str(raw.get("provider_id") or ""),
                        ssh_user=str(raw.get("ssh_user") or inventory.ssh_user))


def save_state(inventory: FleetInventory, op: OperatorHost) -> None:
    path = state_path(inventory)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(yaml.safe_dump({"ssh": op.ssh, "public_ip": op.public_ip,
                                    "provider_id": op.provider_id, "ssh_user": op.ssh_user}))
    path.chmod(0o600)


def require_state(inventory: FleetInventory) -> OperatorHost:
    op = load_state(inventory)
    if op is None:
        raise ConfigError("no operator host for this workshop (run `rodeo fleet operator up` first)")
    return op


# ---------------------------------------------------------------- lifecycle

def _aws(inventory: FleetInventory) -> tuple[dict[str, Any], Any]:
    cfg = require_provider(inventory)
    if str(cfg.get("type")) != "aws":
        raise ConfigError("fleet operator runs on AWS workshops (provider.type: aws)")
    from ..providers import get_provider

    return cfg, get_provider("aws")


def provision(inventory: FleetInventory, *, instance_type: str | None = None) -> OperatorHost:
    cfg, provider = _aws(inventory)
    host = provider.provision_operator(cfg, workshop=inventory.name, ssh_user=inventory.ssh_user,
                                       instance_type=instance_type)
    op = OperatorHost(ssh=host.ssh, public_ip=host.public_ip, provider_id=host.provider_id or "",
                      ssh_user=inventory.ssh_user)
    save_state(inventory, op)
    return op


def deprovision(inventory: FleetInventory) -> list[Any]:
    cfg, provider = _aws(inventory)
    results = provider.deprovision_operator(cfg, workshop=inventory.name)
    state_path(inventory).unlink(missing_ok=True)
    return results


def bootstrap_script(inventory: FleetInventory) -> str:
    """Root script: rodeo (same source as the fleet) and the AWS CLI v2.

    Root, like on the lab hosts: install.sh needs it, and keeping every
    operator step in root's home means one place for the fleet state."""
    from ..install_source import bootstrap_fragment

    rodeo = bootstrap_fragment(install_url=inventory.install_url, ref=inventory.ref)
    return f"""set -euo pipefail
if ! command -v curl >/dev/null || ! command -v gpg >/dev/null || ! command -v unzip >/dev/null; then
  ZYPP_LOCK_TIMEOUT=600 zypper -n -q in curl gpg2 unzip
fi
{rodeo}
RODEO_BIN=$(readlink -f "$(command -v rodeo)")
PY=$(head -1 "$RODEO_BIN" | sed 's/^#!//')
# v2 at /usr/local/bin/aws: the distro's aws (v1) has no `aws login`.
if ! {AWS} --version 2>&1 | grep -q '^aws-cli/2'; then
  "$PY" -c 'from rodeo.awscli import install_cli; print(install_cli())'
fi
mkdir -p "$HOME/{REMOTE_WORKSHOPS}"
chmod 700 "$HOME/{REMOTE_WORKSHOPS}"
rodeo --version
{AWS} --version
echo OPERATOR_READY
"""


def bootstrap(inventory: FleetInventory, op: OperatorHost) -> str:
    res = run_remote(inventory, op.as_fleet_host(), ["bash", "-c", bootstrap_script(inventory)],
                     timeout=1200)
    if not res.ok or "OPERATOR_READY" not in res.stdout:
        raise ConfigError(f"operator bootstrap failed (exit {res.rc}): {(res.stderr or '').strip()[-600:]}")
    return res.stdout.replace("OPERATOR_READY", "").strip()


# ---------------------------------------------------------------- push / pull

def remote_workshop_dir(inventory: FleetInventory) -> str:
    """Relative to the operator's home: ``rodeo-operator/<workshop>``."""
    return f"{REMOTE_WORKSHOPS}/{_safe(inventory.name)}"


def _safe(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]", "-", name).strip(".-")
    if not out:
        raise ConfigError(f"workshop name {name!r} has no usable characters")
    return out


def state_dirs(inventory: FleetInventory) -> list[str]:
    """What the fleet commands need besides the workshop dir, relative to ``~``:
    the lab's SSH key, the fleet's known_hosts and, for a player workshop, the
    event certificate."""
    from .. import paths
    from ..ssh_key import lab_ssh_dir

    home = paths.invoking_home()
    dirs = [lab_ssh_dir(inventory.name), known_hosts_path(inventory.name).parent]
    if inventory.player is not None:
        from .cert import cert_dir

        dirs.append(cert_dir(inventory.player.domain, inventory.player.staging))
    try:
        return [str(PurePosixPath(*d.relative_to(home).parts)) for d in dirs]
    except ValueError as exc:
        raise ConfigError(f"rodeo state is not under {home}: run this as your own user, not sudo") from exc


def _add_tree(tar: tarfile.TarFile, src: Path, arc: str, *, skip_operator_state: bool = False) -> None:
    if not src.exists():
        return
    for path in sorted([src, *src.rglob("*")]):
        if path.is_symlink() or path.name in _PULL_SKIP:
            continue
        if skip_operator_state and path.name == "operator.yaml":
            continue
        rel = path.relative_to(src)
        tar.add(path, arcname=str(PurePosixPath(arc, *rel.parts)), recursive=False)


def push_archive(inventory: FleetInventory, inventory_path: Path) -> bytes:
    """tar.gz of the workshop dir (as ``rodeo-operator/<name>``) and the state dirs."""
    from .. import paths

    home = paths.invoking_home()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        _add_tree(tar, Path(inventory_path).resolve().parent, remote_workshop_dir(inventory))
        for rel in state_dirs(inventory):
            _add_tree(tar, home / rel, rel, skip_operator_state=True)
    data = buf.getvalue()
    if len(data) > MAX_PUSH_BYTES:
        raise ConfigError(f"the workshop dir packs to {len(data) // 2**20} MiB; keep big files "
                          f"(images, logs) out of {Path(inventory_path).resolve().parent}")
    return data


def as_root(command: str) -> str:
    """Run *command* as root with root's HOME (the operator's state lives there)."""
    return f"sudo -n -H bash -c {shlex.quote(command)}"


def _ssh(inventory: FleetInventory, op: OperatorHost, command: str, *, stdin: bytes | None = None,
         timeout: float = 600) -> subprocess.CompletedProcess:
    known_hosts_path(inventory.name).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return subprocess.run(ssh_argv(inventory, op.as_fleet_host(), as_root(command)), input=stdin,
                          capture_output=True, timeout=timeout)


def push(inventory: FleetInventory, inventory_path: Path) -> int:
    op = require_state(inventory)
    data = push_archive(inventory, inventory_path)
    # umask 077: keys, certs and secrets land private, as they are here.
    proc = _ssh(inventory, op, "umask 077 && tar -xzf - -C \"$HOME\" --no-same-owner", stdin=data)
    if proc.returncode != 0:
        raise ConfigError(f"push failed: {proc.stderr.decode(errors='replace').strip()[-400:]}")
    return len(data)


def _allowed(name: str, prefixes: Sequence[str]) -> bool:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts:
        return False
    return any(str(p) == pre or str(p).startswith(pre + "/") for pre in prefixes)


def pull(inventory: FleetInventory, inventory_path: Path) -> list[str]:
    """Bring the workshop dir and state dirs back. Keeps a backup of the local copy first."""
    from .. import paths

    op = require_state(inventory)
    remote_dir = remote_workshop_dir(inventory)
    wanted = [remote_dir, *state_dirs(inventory)]
    cmd = "cd \"$HOME\" && tar -czf - --ignore-failed-read " + " ".join(shlex.quote(w) for w in wanted) + " 2>/dev/null"
    proc = _ssh(inventory, op, cmd, timeout=600)
    if not proc.stdout:
        raise ConfigError(f"pull failed: {proc.stderr.decode(errors='replace').strip()[-400:]}")
    local_dir = Path(inventory_path).resolve().parent
    home = paths.invoking_home()
    backup = backup_local(inventory, inventory_path)
    written: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not (m.isfile() or m.isdir()) or not _allowed(m.name, wanted):
                continue  # links, devices and anything outside what we asked for
            p = PurePosixPath(m.name)
            if str(p) == remote_dir or str(p).startswith(remote_dir + "/"):
                dest = local_dir.joinpath(*p.parts[len(PurePosixPath(remote_dir).parts):])
            else:
                dest = home.joinpath(*p.parts)
            if m.isdir():
                dest.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            src = tar.extractfile(m)
            if src is None:
                continue
            tmp = dest.with_name(f".{dest.name}.pull")
            tmp.write_bytes(src.read())
            tmp.chmod(m.mode & 0o700 or 0o600)
            tmp.replace(dest)
            written.append(str(dest))
    if backup:
        written.append(f"(backup of the local copy: {backup})")
    return written


def backup_local(inventory: FleetInventory, inventory_path: Path) -> Path | None:
    src = Path(inventory_path).resolve().parent
    if not src.is_dir():
        return None
    dest_dir = state_path(inventory).parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = dest_dir / f"{time.strftime('%Y%m%dT%H%M%S')}.tar.gz"
    with tarfile.open(dest, "w:gz") as tar:
        _add_tree(tar, src, src.name)
    dest.chmod(0o600)
    return dest


# ---------------------------------------------------------------- login / run / logs

def login_command(inventory: FleetInventory) -> str:
    region = str(require_provider(inventory).get("region") or "")
    if not re.match(r"^[a-z]{2}(-[a-z]+)+-\d$", region):
        raise ConfigError(f"provider.region {region!r} is not an AWS region")
    return (f"{AWS} configure set region {region} && {AWS} login --remote && "
            f"{AWS} sts get-caller-identity --query Arn --output text")


def login(inventory: FleetInventory) -> int:
    """Interactive: the URL and the code prompt are for the person at the terminal."""
    op = require_state(inventory)
    argv = ssh_argv(inventory, op.as_fleet_host(), as_root(login_command(inventory)))
    argv[1:1] = ["-t"]
    return subprocess.run(argv).returncode


_FILE_FLAGS = {"-f", "--file"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml$")
EXIT_FILE = ".operator-exit"   # in the pushed workshop dir: the last run's exit code


def run_argv(inventory: FleetInventory, args: Sequence[str]) -> list[str]:
    """The fleet command's own arguments, checked: no -f, no operator-in-operator."""
    if not args:
        raise ConfigError("say which fleet command to run, e.g. `-- up --track-ref <sha>`")
    if any(a in _FILE_FLAGS or a.startswith("--file=") for a in args):
        raise ConfigError("leave out -f: the operator runs the workshop you pushed")
    if args[0] == "operator":
        raise ConfigError("the operator host does not run operator commands")
    return list(args)


def run_command(inventory: FleetInventory, inventory_path: Path, args: Sequence[str],
                laptop_ip: str = "") -> str:
    """Root shell: a transient systemd unit, so the run survives SSH.

    The unit writes its exit code to the workshop dir, because a unit that
    ends well is gone from systemd. *laptop_ip* stays allowed on the labs'
    SSH while the operator host is the caller (see providers.aws._also_allowed)."""
    import ipaddress

    if laptop_ip:
        laptop_ip = str(ipaddress.IPv4Address(laptop_ip))
    args = run_argv(inventory, args)
    name = Path(inventory_path).name
    if not _NAME_RE.match(name):
        raise ConfigError(f"inventory file name {name!r}: letters, digits, . _ - and .yaml only")
    wdir = f'"$HOME"/{remote_workshop_dir(inventory)}'   # safe characters only (_safe)
    fleet = f"rodeo fleet {shlex.quote(args[0])} -f {wdir}/{name} " + " ".join(shlex.quote(a) for a in args[1:])
    body = f"{fleet}; rc=$?; echo $rc > {wdir}/{EXIT_FILE}; exit $rc"
    also = f"--setenv {ALSO_ALLOW_ENV}={laptop_ip} " if laptop_ip else ""
    return f"""set -euo pipefail
if systemctl is-active -q {UNIT}; then
  echo "a fleet command is still running on the operator host (rodeo fleet operator logs)" >&2; exit 3
fi
[ -d "$HOME/{remote_workshop_dir(inventory)}" ] || {{ echo "nothing pushed yet (rodeo fleet operator push)" >&2; exit 2; }}
rm -f "$HOME/{remote_workshop_dir(inventory)}/{EXIT_FILE}"
systemctl reset-failed {UNIT} 2>/dev/null || true
systemd-run --unit {UNIT} --working-directory "$HOME" --setenv HOME="$HOME" {also}-- bash -lc {shlex.quote(body)}
echo RUN_STARTED
"""


def run(inventory: FleetInventory, inventory_path: Path, args: Sequence[str]) -> None:
    op = require_state(inventory)
    from ..providers.aws import _detect_caller_ip

    try:
        laptop = _detect_caller_ip()
    except ConfigError:  # without it the laptop only loses lab SSH until its next fleet run
        laptop = ""
    script = run_command(inventory, inventory_path, args, laptop_ip=laptop)
    res = run_remote(inventory, op.as_fleet_host(), ["bash", "-c", script], timeout=120)
    if not res.ok or "RUN_STARTED" not in res.stdout:
        raise ConfigError((res.stderr or "").strip()[-400:] or f"could not start the run (exit {res.rc})")


def logs(inventory: FleetInventory, *, follow: bool = True, lines: int = 200) -> int:
    op = require_state(inventory)
    cmd = f"journalctl -u {UNIT} -o cat --no-pager -n {int(lines)}" + (" -f" if follow else "")
    argv = ssh_argv(inventory, op.as_fleet_host(), as_root(cmd))
    if follow:
        argv[1:1] = ["-t"]
    try:
        return subprocess.run(argv).returncode
    except KeyboardInterrupt:
        return 0


def status(inventory: FleetInventory) -> str:
    """``running``, ``done``, ``failed (exit N)`` or ``none``."""
    op = require_state(inventory)
    exit_file = f"$HOME/{remote_workshop_dir(inventory)}/{EXIT_FILE}"
    res = run_remote(inventory, op.as_fleet_host(), ["bash", "-c",
                     f'systemctl is-active -q {UNIT} && echo running || cat "{exit_file}" 2>/dev/null || echo none'],
                     timeout=60)
    return status_text(res.stdout)


def status_text(out: str) -> str:
    word = (out.strip().splitlines() or ["none"])[-1].strip()
    if word in ("running", "none"):
        return word
    return "done" if word == "0" else f"failed (exit {word})"
