"""Load and merge rodeo-plan.yaml + ~/.rodeo/secrets.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_DEFAULTS: dict[str, Any] = {
    "name": "suse-virt-rodeo",
    "network": {
        "mode": "nat",
        "vip": "192.168.122.10",
        "rancher_ip": "192.168.122.9",
        "gateway": "192.168.122.1",
        "dns_domain": "aerogrid.com",
    },
    "resources": {
        "harvester": {"memory_mib": 16384, "vcpu": 8, "disk_gb": 270},
        "rancher": {"memory_mib": 8192, "vcpu": 4, "disk_gb": 60},
    },
    "versions": {
        "harvester": "1.8.0",
        "rancher": "2.13.1",
        "k3s": "v1.31.4+k3s1",
        "cert_manager": "v1.16.2",
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


def _resolve_secrets(cfg: dict, secrets: dict) -> dict:
    """Replace ??key placeholders with values from secrets."""
    def _walk(obj: Any) -> Any:
        if isinstance(obj, str) and obj.startswith("??"):
            key = obj[2:]
            return secrets.get(key, obj)
        if isinstance(obj, dict):
            return {k: _walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(i) for i in obj]
        return obj
    return _walk(cfg)


def load_config(plan_path: str | Path = "rodeo-plan.yaml") -> dict:
    plan_path = Path(plan_path)
    cfg = dict(_DEFAULTS)

    if plan_path.exists():
        with open(plan_path) as f:
            plan = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, plan)

    secrets: dict = {}
    if _SECRETS_PATH.exists():
        with open(_SECRETS_PATH) as f:
            secrets = yaml.safe_load(f) or {}

    cfg = _resolve_secrets(cfg, secrets)

    # RODEO_ANSIBLE_PATH env override
    env_path = os.environ.get("RODEO_ANSIBLE_PATH")
    if env_path:
        cfg["ansible"]["path"] = env_path

    return cfg


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


# Keep old name for backward compatibility in deploy.py v0.1
find_ansible_path = find_ansible_root
