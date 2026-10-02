"""Plan ownership markers on libvirt domains."""
from __future__ import annotations

from pathlib import Path

import rodeo
from rodeo.engine.libvirt import names_safe_to_clean, plan_from_domain_xml

_ANSIBLE = Path(rodeo.__file__).parent / "data" / "ansible"


def test_plan_marker_round_trip():
    xml = "<domain><description>rodeo:plan=harvester-lab</description></domain>"
    assert plan_from_domain_xml(xml) == "harvester-lab"
    assert plan_from_domain_xml("<domain/>") is None


def test_clean_skips_foreign_plan_and_keeps_legacy():
    xml_by_name = {
        "harvester1": "<description>rodeo:plan=other</description>",
        "rancher": "<description>rodeo:plan=mine</description>",
        "edge1": "<domain/>",
    }
    safe, foreign = names_safe_to_clean(
        ["harvester1", "rancher", "edge1"], xml_by_name, "mine"
    )
    assert safe == ["rancher", "edge1"]
    assert foreign == [("harvester1", "other")]


def test_vm_template_stamps_plan():
    xml = (_ANSIBLE / "roles" / "vms" / "templates" / "vm.xml.j2").read_text()
    assert "rodeo:plan={{ rodeo_plan_name | default('default') }}" in xml


def test_nfs_role_has_no_fixed_password():
    text = (_ANSIBLE / "roles" / "kvm_host" / "tasks" / "nfs.yml").read_text()
    assert "weakestLink" not in text
    assert "no_root_squash" not in text
    assert "state: absent" in text
    assert 'mode: "0755"' in text
    # root_squash maps client root to nobody; the export must be writable by it.
    assert 'owner: "65534"' in text
