"""Resolve ``rodeo ssh`` targets: VM, host, or host/vm hop."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError
from .fleet.inventory import load_inventory
from .providers.remote_up import load_aws_host_state
from .ssh_key import ensure_rodeo_ssh_key, resolve_ssh_identity

# The KVM host's own key, baked into every nested VM's cloud-init by
# common/ensure_ssh_key.yml — the only identity nested VMs ever trust.
_HOST_ROOT_SSH_KEY = Path("/root/.ssh/id_ed25519")


@dataclass(frozen=True)
class SshTarget:
    """Resolved OpenSSH destination."""

    user: str
    host: str
    identity_file: str
    jump_user: str | None = None
    jump_host: str | None = None


def parse_ssh_target_arg(raw: str) -> tuple[str | None, str]:
    """Split ``host/vm`` or return ``(None, vm_or_host)``."""
    text = raw.strip()
    if "/" in text:
        host_id, _, vm = text.partition("/")
        host_id = host_id.strip()
        vm = vm.strip()
        if not host_id or not vm:
            raise ConfigError("use host/vm — e.g. student-01/rancher")
        return host_id, vm
    return None, text


def default_identity(
    cfg: dict[str, Any] | None = None,
    *,
    key: str | None = None,
    prefer_root_key: bool = False,
) -> str:
    if key:
        return str(Path(key).expanduser())
    if cfg:
        plan_key = cfg.get("ssh", {}).get("identity_file")
        if plan_key:
            return str(Path(str(plan_key)).expanduser())
    managed = ensure_rodeo_ssh_key()
    root_key = _HOST_ROOT_SSH_KEY
    if (prefer_root_key or os.geteuid() == 0) and root_key.is_file():
        if os.access(root_key, os.R_OK):
            return str(root_key)
        if prefer_root_key:
            # Nested VMs only ever trust this key (baked into their cloud-init
            # by the common/ensure_ssh_key Ansible task) — the operator's
            # managed key is a different identity, for host/EC2-level hops.
            # Fail clearly here rather than silently falling back to a key
            # the VM doesn't trust, which just degrades into an unexplained
            # interactive password prompt.
            raise ConfigError(
                f"{root_key} is the key nested VMs trust, but it isn't readable "
                "as the current user — re-run with sudo (e.g. `sudo rodeo ssh "
                "<vm>`)."
            )
    return str(managed)


def find_workshop_path(explicit: str | None = None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for name in ("workshop.yaml", "workshop.yml"):
        p = Path.cwd() / name
        if p.is_file():
            return p
    return None


def resolve_kvm_host(
    host_id: str,
    *,
    cfg: dict[str, Any] | None = None,
    workshop_path: Path | None = None,
) -> tuple[str, str, str]:
    """Return ``(ssh_user, ssh_host, identity)`` for a KVM/EC2 host id."""
    identity = resolve_ssh_identity()
    ws = workshop_path or find_workshop_path()
    if ws is not None:
        inv = load_inventory(ws)
        for h in inv.hosts:
            if h.id == host_id:
                user = h.ssh_user or inv.ssh_user
                target = h.ssh
                if "@" in target:
                    user, _, host = target.partition("@")
                    return user, host, identity
                return user, target, identity
        # host not yet in inventory — fall through to AWS state

    plan_name = str((cfg or {}).get("name") or "")
    if plan_name:
        state = load_aws_host_state(plan_name)
        if state and str(state.get("host_id") or "") == host_id:
            provider = (cfg or {}).get("provider") or {}
            user = str(provider.get("ssh_user") or "ec2-user")
            host = str(state.get("public_ip") or state.get("ssh") or "")
            if host:
                return user, host, identity

    # Any aws-host state matching host_id (single-host primary).
    from .paths import rodeo_state_dir

    state_dir = rodeo_state_dir()
    if state_dir.is_dir():
        for path in state_dir.glob("*-aws-host.yaml"):
            raw = yaml.safe_load(path.read_text()) or {}
            if not isinstance(raw, dict):
                continue
            if str(raw.get("host_id") or "") != host_id:
                continue
            host = str(raw.get("public_ip") or raw.get("ssh") or "")
            if not host:
                continue
            user = "ec2-user"
            if cfg and isinstance(cfg.get("provider"), dict):
                user = str(cfg["provider"].get("ssh_user") or user)
            return user, host, identity

    raise ConfigError(
        f"unknown host {host_id!r} — add it to workshop.yaml hosts[] "
        "or provision with rodeo up --target aws / fleet provision"
    )


def resolve_vm_on_plan(cfg: dict[str, Any], vm: str) -> tuple[str, str]:
    vms = cfg.get("vms") or {}
    if vm not in vms:
        known = ", ".join(vms) if vms else "(none)"
        raise ConfigError(f"unknown VM {vm!r}. Known: {known}")
    info = vms[vm]
    return str(info.get("user") or "root"), str(info["ip"])


def fetch_remote_vm(
    *,
    jump_user: str,
    jump_host: str,
    identity: str,
    lab_dir: str,
    vm: str,
) -> tuple[str, str]:
    """Load VM user/ip from remote lab plan via SSH."""
    import shlex
    import subprocess

    from .ssh import ssh_opts

    py = (
        "import pathlib,sys,yaml;"
        f"p=pathlib.Path({lab_dir!r})/'rodeo-plan.yaml';"
        "d=yaml.safe_load(p.read_text()) or {};"
        "v=(d.get('vms') or {}).get(sys.argv[1]);"
        "raise SystemExit(2) if not v else print(v.get('user','root'), v['ip'])"
    )
    argv = [
        "ssh",
        "-i",
        identity,
        *ssh_opts(),
        f"{jump_user}@{jump_host}",
        f"python3 -c {shlex.quote(py)} {shlex.quote(vm)}",
    ]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        raise ConfigError(f"cannot resolve VM {vm!r} on {jump_host}: {err[:300]}")
    parts = proc.stdout.strip().split()
    if len(parts) < 2:
        raise ConfigError(f"cannot resolve VM {vm!r} on {jump_host}: bad remote output")
    return parts[0], parts[1]


def build_ssh_target(
    target_arg: str,
    *,
    cfg: dict[str, Any],
    key: str | None = None,
    login_user: str | None = None,
    workshop: str | None = None,
) -> SshTarget:
    """Build destination for ``rodeo ssh`` (vm | host | host/vm)."""
    identity = default_identity(cfg, key=key)
    host_id, name = parse_ssh_target_arg(target_arg)
    workshop_path = find_workshop_path(workshop)

    if host_id is not None:
        # host/vm hop
        ju, jh, ident = resolve_kvm_host(
            host_id, cfg=cfg, workshop_path=workshop_path
        )
        identity = key or ident or identity
        lab_dir = "/root/rodeo-lab"
        if workshop_path is not None:
            inv = load_inventory(workshop_path)
            lab_dir = inv.lab_dir
        elif isinstance(cfg.get("provider"), dict) and cfg["provider"].get("lab_dir"):
            lab_dir = str(cfg["provider"]["lab_dir"])
        # Prefer local plan VMs when present (on-host); else fetch remote.
        vms = cfg.get("vms") or {}
        if name in vms:
            user, ip = resolve_vm_on_plan(cfg, name)
        else:
            user, ip = fetch_remote_vm(
                jump_user=ju,
                jump_host=jh,
                identity=identity,
                lab_dir=lab_dir,
                vm=name,
            )
        if login_user:
            user = login_user
        return SshTarget(
            user=user,
            host=ip,
            identity_file=identity,
            jump_user=ju,
            jump_host=jh,
        )

    # Bare name: VM on local plan, else KVM host id
    vms = cfg.get("vms") or {}
    if name in vms:
        user, ip = resolve_vm_on_plan(cfg, name)
        if login_user:
            user = login_user
        # Nested VMs only trust the host's /root/.ssh/id_ed25519 (see
        # ensure_ssh_key.yml), never the operator's managed key — resolve it
        # explicitly here regardless of the invoking user's euid.
        vm_identity = default_identity(cfg, key=key, prefer_root_key=True)
        return SshTarget(user=user, host=ip, identity_file=vm_identity)

    try:
        user, host, ident = resolve_kvm_host(
            name, cfg=cfg, workshop_path=workshop_path
        )
    except ConfigError:
        known = ", ".join(vms) if vms else "(none)"
        raise ConfigError(
            f"unknown target {name!r}. VMs: {known}. "
            "For a nested VM on a remote host use host/vm "
            "(e.g. student-01/rancher)."
        ) from None
    if login_user:
        user = login_user
    return SshTarget(
        user=user,
        host=host,
        identity_file=key or ident or identity,
    )


def ssh_argv_for(target: SshTarget, *, remote_cmd: str | None = None) -> list[str]:
    from .ssh import ssh_opts

    argv = ["ssh", "-i", target.identity_file, *ssh_opts()]
    # Drop BatchMode for interactive shells (ssh_opts includes BatchMode=yes).
    if not remote_cmd:
        cleaned: list[str] = []
        i = 0
        while i < len(argv):
            if argv[i] == "-o" and i + 1 < len(argv) and argv[i + 1] == "BatchMode=yes":
                i += 2
                continue
            cleaned.append(argv[i])
            i += 1
        argv = cleaned

    if target.jump_host:
        jump = f"{target.jump_user}@{target.jump_host}" if target.jump_user else target.jump_host
        argv.extend(["-o", f"ProxyJump={jump}"])
    argv.append(f"{target.user}@{target.host}")
    if remote_cmd:
        argv.append(remote_cmd)
    return argv
