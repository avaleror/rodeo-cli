"""Load and merge rodeo-plan.yaml + ~/.rodeo/secrets.yaml."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml


# Base defaults that apply to every rodeo type.
# Profile-specific defaults (vms, resources, versions) are merged from the profile.
_BASE_DEFAULTS: dict[str, Any] = {
    "type": "suse-virt",
    "name": "suse-virt-rodeo",
    "deployment_target": "baremetal",  # instruqt | baremetal
    "network": {
        "mode": "nat",
        "vip": "192.168.122.10",
        "rancher_ip": "192.168.122.9",
        "gateway": "192.168.122.1",
        "dns_domain": "aerogrid.com",
    },
    "storage": {"image_dir": "/var/lib/libvirt/images"},
    "libvirt": {"uri": "qemu:///system"},
    "ansible": {
        "path": None,
        "inventory": "deployer/inventory.local",
    },
    "credentials": {
        "harvester_os_password": None,
        "lab_admin_password": None,
    },
}

_SECRETS_PATH = Path.home() / ".rodeo" / "secrets.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _resolve_secret_value(value: str, secrets: dict) -> str:
    """Resolve one ??placeholder. On failure the literal is kept so
    validate_config() fails closed with a clear message.

    Supported forms:
      ??key                  -> ~/.rodeo/secrets.yaml lookup
      ??env:NAME             -> environment variable
      ??file:/path           -> first line of a file (e.g. a mounted secret)
      ??cmd:some command     -> stdout of a shell command (pass, op, vault...)
    """
    spec = value[2:]
    if spec.startswith("env:"):
        return os.environ.get(spec[4:]) or value
    if spec.startswith("file:"):
        try:
            content = Path(spec[5:]).read_text().strip()
            return content or value
        except OSError:
            return value
    if spec.startswith("cmd:"):
        try:
            r = subprocess.run(
                spec[4:], shell=True, capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return value
    return secrets.get(spec, value)


def _resolve_secrets(cfg: dict, secrets: dict) -> dict:
    """Replace ??placeholders throughout the config."""
    def _walk(obj: Any) -> Any:
        if isinstance(obj, str) and obj.startswith("??"):
            return _resolve_secret_value(obj, secrets)
        if isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(i) for i in obj]
        return obj
    return _walk(cfg)


def load_config(plan_path: str | Path = "rodeo-plan.yaml") -> dict:
    plan_path = Path(plan_path)
    plan: dict = {}
    if plan_path.exists():
        with open(plan_path) as f:
            plan = yaml.safe_load(f) or {}

    # Determine type early so profile defaults can be merged before plan overrides.
    type_name = plan.get("type", _BASE_DEFAULTS["type"])
    try:
        from .profiles import get_profile
        profile_defaults = get_profile(type_name).default_cfg()
    except (ImportError, ValueError):
        profile_defaults = {}

    cfg = _deep_merge(_BASE_DEFAULTS, profile_defaults)
    cfg = _deep_merge(cfg, plan)

    secrets: dict = {}
    if _SECRETS_PATH.exists():
        with open(_SECRETS_PATH) as f:
            secrets = yaml.safe_load(f) or {}

    cfg = _resolve_secrets(cfg, secrets)

    env_path = os.environ.get("RODEO_ANSIBLE_PATH")
    if env_path:
        cfg["ansible"]["path"] = env_path

    return cfg


def validate_config(cfg: dict) -> None:
    """Raise ValueError on unresolved ??placeholders or missing/empty credentials."""
    creds = cfg.get("credentials", {})
    unresolved = [k for k, v in creds.items() if isinstance(v, str) and v.startswith("??")]
    if unresolved:
        raise ValueError(
            f"Secrets not resolved: {', '.join(unresolved)}\n"
            "For ??key: edit ~/.rodeo/secrets.yaml or run: rodeo init\n"
            "For ??env:/??file:/??cmd:: the source returned nothing — "
            "check the variable, file, or command."
        )
    empty = [
        k for k, v in creds.items()
        if v is None or (isinstance(v, str) and (not v.strip() or v == "CHANGE_ME"))
    ]
    if empty:
        raise ValueError(
            f"Credentials are empty: {', '.join(empty)}\n"
            "An empty password would be baked into the Harvester config ISOs.\n"
            "Set values in rodeo-plan.yaml (??key) and ~/.rodeo/secrets.yaml, or run: rodeo init"
        )
    target = cfg.get("deployment_target", "baremetal")
    if target not in ("instruqt", "baremetal"):
        raise ValueError(
            f"Invalid deployment_target '{target}' — use 'instruqt' or 'baremetal'."
        )


_BUNDLED_DATA = Path(__file__).parent / "data"


def find_ansible_root(cfg: dict) -> Path | None:
    """Return the directory containing ansible/playbook.yml and deployer/.

    Search order:
      1. cfg['ansible']['path'] or RODEO_ANSIBLE_PATH env
      2. Bundled data shipped with rodeo-cli
      3. Current working directory
      4. ~/instruqt-virtualization (dev checkout)
    """
    candidates = [
        cfg["ansible"].get("path"),
        os.environ.get("RODEO_ANSIBLE_PATH"),
        str(_BUNDLED_DATA),
        ".",
        str(Path.home() / "instruqt-virtualization"),
    ]
    for c in candidates:
        if c is None:
            continue
        p = Path(c)
        if (p / "ansible" / "playbook.yml").exists():
            return p
    return None
