"""kvm_host NVMe pool tasks must be gated and idempotent (no unguarded mkfs)."""
from __future__ import annotations

from pathlib import Path

import yaml

import rodeo

_MAIN = Path(rodeo.__file__).parent / "data" / "ansible" / "roles" / "kvm_host" / "tasks" / "main.yml"
_NVME = Path(rodeo.__file__).parent / "data" / "ansible" / "roles" / "kvm_host" / "tasks" / "nvme_storage.yml"


def test_main_imports_nvme_storage_when_aws_or_backend():
    doc = yaml.safe_load(_MAIN.read_text())
    nvme = next(t for t in doc if "nvme" in t.get("name", "").lower())
    assert "nvme_storage.yml" in str(nvme.get("ansible.builtin.import_tasks", ""))
    when = nvme["when"]
    assert "host_storage_backend" in when
    assert "deployment_target" in when


def test_nvme_format_only_when_no_fstype():
    text = _NVME.read_text()
    assert "filesystem:" in text or "ansible.builtin.filesystem" in text
    assert "blkid" in text
    assert "_pool_fstype" in text
    assert "when:" in text
