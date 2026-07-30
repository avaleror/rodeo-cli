"""Single-host AWS acquire + remote ``rodeo up`` (deployment_target: aws)."""
from __future__ import annotations

import shlex
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import yaml

from ..config import ConfigError
from ..fleet.inventory import FleetHost, FleetInventory
from ..fleet.ssh_exec import run_remote
from ..paths import rodeo_state_dir
from ..ssh_key import resolve_ssh_identity
from .base import SINGLE_HOST_ID, ProvisionSpec, ProvisionedHost
from .registry import get_provider

_DEFAULT_INSTALL = (
    "https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh"
)
_DEFAULT_LAB_DIR = "/root/rodeo-lab"


def on_ec2(*, timeout: float = 0.4) -> bool:
    """True when running on an EC2 instance (IMDSv2, IMDSv1, or DMI fallback)."""
    if _on_ec2_imds(timeout=timeout):
        return True
    return _on_ec2_dmi()


def _on_ec2_imds(*, timeout: float) -> bool:
    """Probe instance metadata; prefer IMDSv2 token, fall back to IMDSv1."""
    try:
        token_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        with urllib.request.urlopen(token_req, timeout=timeout) as token_resp:
            token = token_resp.read().decode("utf-8", errors="replace").strip()
        if token:
            meta_req = urllib.request.Request(
                "http://169.254.169.254/latest/meta-data/",
                headers={"X-aws-ec2-metadata-token": token},
            )
            with urllib.request.urlopen(meta_req, timeout=timeout) as resp:
                return int(getattr(resp, "status", 200) or 200) < 400
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        pass
    try:
        req = urllib.request.Request("http://169.254.169.254/latest/meta-data/")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200) or 200) < 400
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _on_ec2_dmi() -> bool:
    """Best-effort EC2 detection via DMI / sysfs when IMDS is unreachable."""
    for path in (
        Path("/sys/devices/virtual/dmi/id/product_uuid"),
        Path("/sys/hypervisor/uuid"),
    ):
        try:
            val = path.read_text().strip().lower()
        except OSError:
            continue
        if val.startswith("ec2"):
            return True
    for path in (
        Path("/sys/devices/virtual/dmi/id/bios_vendor"),
        Path("/sys/devices/virtual/dmi/id/sys_vendor"),
    ):
        try:
            val = path.read_text().strip().lower()
        except OSError:
            continue
        if "amazon" in val:
            return True
    return False


def aws_host_state_path(plan_name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in plan_name)
    return rodeo_state_dir() / f"{safe}-aws-host.yaml"


def save_aws_host_state(plan_name: str, host: ProvisionedHost) -> Path:
    path = aws_host_state_path(plan_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "plan": plan_name,
                "host_id": host.id,
                "ssh": host.ssh,
                "public_ip": host.public_ip,
                "provider_id": host.provider_id,
                "labels": dict(host.labels),
            },
            default_flow_style=False,
            sort_keys=False,
        )
    )
    return path


def load_aws_host_state(plan_name: str) -> dict[str, Any] | None:
    path = aws_host_state_path(plan_name)
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text()) or {}
    return raw if isinstance(raw, dict) else None


def clear_aws_host_state(plan_name: str) -> None:
    path = aws_host_state_path(plan_name)
    if path.is_file():
        path.unlink()


def _provider_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    provider = cfg.get("provider")
    if not isinstance(provider, dict):
        raise ConfigError("deployment_target: aws requires provider:")
    out = dict(provider)
    out["type"] = str(out.get("type") or "aws").strip().lower()
    return out


def provision_primary(
    cfg: dict[str, Any],
    *,
    get_provider_fn: Callable[[str], Any] | None = None,
) -> ProvisionedHost:
    """Create or reuse the single EC2 host for this plan."""
    provider_cfg = _provider_cfg(cfg)
    identity = resolve_ssh_identity(str(provider_cfg.get("identity_file") or "") or None)
    provider_cfg = dict(provider_cfg)
    provider_cfg["identity_file"] = identity
    ssh_user = str(provider_cfg.get("ssh_user") or "ec2-user")
    plan_name = str(cfg.get("name") or "rodeo")
    spec = ProvisionSpec(
        workshop=plan_name,
        host_ids=[SINGLE_HOST_ID],
        ssh_user=ssh_user,
        identity_file=identity,
        wait_ssh=True,
        extra_labels={"scope": "single-host"},
    )
    getter = get_provider_fn or get_provider
    provider = getter(str(provider_cfg["type"]))
    hosts = provider.provision(spec, provider_cfg)
    if not hosts:
        raise ConfigError("AWS provision returned no hosts")
    host = hosts[0]
    save_aws_host_state(plan_name, host)
    return host


def _fleet_inventory_for(
    cfg: dict[str, Any],
    host: ProvisionedHost,
) -> tuple[FleetInventory, FleetHost]:
    provider_cfg = _provider_cfg(cfg)
    identity = resolve_ssh_identity(str(provider_cfg.get("identity_file") or "") or None)
    ssh_user = str(provider_cfg.get("ssh_user") or "ec2-user")
    plan_name = str(cfg.get("name") or "rodeo")
    inv = FleetInventory(
        name=plan_name,
        lab_dir=str(provider_cfg.get("lab_dir") or _DEFAULT_LAB_DIR),
        defaults={
            "ssh_user": ssh_user,
            "identity_file": identity,
        },
        hosts=[],
        install_url=str(provider_cfg.get("install_url") or _DEFAULT_INSTALL),
    )
    fh = FleetHost(
        id=host.id,
        ssh=host.ssh,
        public_ip=host.public_ip,
        labels=dict(host.labels),
    )
    return inv, fh


def remote_up_script(
    *,
    lab_dir: str,
    profile: str | None,
    install_url: str = _DEFAULT_INSTALL,
) -> str:
    """Bootstrap + remote ``rodeo up --target baremetal`` (guest runs as baremetal)."""
    lab = shlex.quote(lab_dir)
    url = shlex.quote(install_url)
    profile_bits = ""
    if profile:
        profile_bits = f"--profile {shlex.quote(profile)} "
    return (
        "set -euo pipefail; "
        "if ! command -v rodeo >/dev/null 2>&1; then "
        f"curl -fsSL {url} | bash; "
        "fi; "
        "command -v rodeo >/dev/null; "
        f"mkdir -p {lab}; "
        "set +e; "
        f"rodeo up --yes --no-tmux {profile_bits}"
        f"--dir {lab} --target baremetal "
        f"2>&1 | tee -a \"$HOME/.rodeo/logs/aws-up.log\"; "
        'ec=${PIPESTATUS[0]}; set -e; '
        'echo AWS_UP_EXIT:$ec; '
        "exit \"$ec\""
    )


def run_remote_up(
    cfg: dict[str, Any],
    host: ProvisionedHost,
    *,
    profile: str | None = None,
    timeout: float = 7200.0,
) -> None:
    """SSH to the provisioned host and run ``rodeo up`` as baremetal."""
    inv, fh = _fleet_inventory_for(cfg, host)
    provider_cfg = _provider_cfg(cfg)
    lab_dir = str(provider_cfg.get("lab_dir") or _DEFAULT_LAB_DIR)
    install_url = str(provider_cfg.get("install_url") or _DEFAULT_INSTALL)
    script = remote_up_script(
        lab_dir=lab_dir,
        profile=profile,
        install_url=install_url,
    )
    result = run_remote(
        inv,
        fh,
        ["sudo", "-n", "bash", "-lc", script],
        timeout=timeout,
    )
    if not result.ok:
        msg = (result.stderr or result.stdout or f"exit {result.rc}").strip()
        raise ConfigError(
            f"remote rodeo up failed on {host.public_ip} "
            f"(need passwordless sudo): {msg[:500]}"
        )
    out = result.stdout or ""
    if "AWS_UP_EXIT:" in out:
        for line in reversed(out.splitlines()):
            if line.startswith("AWS_UP_EXIT:"):
                code = line.split(":", 1)[1].strip()
                if code != "0":
                    raise ConfigError(
                        f"remote rodeo up exited {code} on {host.public_ip} "
                        f"(see ~/.rodeo/logs/aws-up.log on the host)"
                    )
                break


def destroy_primary(
    cfg: dict[str, Any],
    *,
    get_provider_fn: Callable[[str], Any] | None = None,
) -> list[Any]:
    """Terminate the ownership-tagged single-host instance for this plan."""
    provider_cfg = _provider_cfg(cfg)
    plan_name = str(cfg.get("name") or "rodeo")
    identity = resolve_ssh_identity(str(provider_cfg.get("identity_file") or "") or None)
    ssh_user = str(provider_cfg.get("ssh_user") or "ec2-user")
    spec = ProvisionSpec(
        workshop=plan_name,
        host_ids=[SINGLE_HOST_ID],
        ssh_user=ssh_user,
        identity_file=identity,
        wait_ssh=False,
    )
    getter = get_provider_fn or get_provider
    provider = getter(str(provider_cfg["type"]))
    results = provider.deprovision(spec, provider_cfg)
    clear_aws_host_state(plan_name)
    return results


def execute_aws_up(
    cfg: dict[str, Any],
    *,
    profile: str | None = None,
    get_provider_fn: Callable[[str], Any] | None = None,
    remote_timeout: float = 7200.0,
) -> ProvisionedHost:
    """Provision (or reuse) primary EC2 host, then remote-run ``rodeo up``."""
    host = provision_primary(cfg, get_provider_fn=get_provider_fn)
    run_remote_up(cfg, host, profile=profile, timeout=remote_timeout)
    return host
