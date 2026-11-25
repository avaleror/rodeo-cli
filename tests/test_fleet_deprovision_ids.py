"""fleet deprovision passes the instance ids provision recorded (labels.provider_id).

Found live 2026-11-25: deprovision crashed with
AttributeError: 'FleetHost' object has no attribute 'provider_id'.
"""
from __future__ import annotations

import yaml

from rodeo.fleet import provision as prov
from rodeo.fleet.inventory import load_inventory


def test_deprovision_reads_instance_ids_from_labels(tmp_path, monkeypatch):
    data = {
        "name": "ws", "lab": {"dir": "/root/lab", "profile": "kvm-hosts"},
        "provider": {"type": "aws", "region": "eu-north-1", "subnet_id": "subnet-1",
                     "instance_type": "m7i.2xlarge", "count": 2},
        "tracking": {"kind": "test", "event": "t"},
        "hosts": [
            {"id": "student-01", "ssh": "10.0.0.1", "labels": {"provider_id": "i-aaa"}},
            {"id": "student-02", "ssh": "10.0.0.2", "labels": {}},
        ],
    }
    p = tmp_path / "workshop.yaml"
    p.write_text(yaml.safe_dump(data))
    seen = {}

    class FakeProvider:
        def deprovision(self, spec, cfg):
            seen["ids"] = spec.known_instance_ids
            return []

    monkeypatch.setattr(prov, "get_provider", lambda name: FakeProvider())
    prov.fleet_deprovision(load_inventory(p))
    assert seen["ids"] == ["i-aaa"]
