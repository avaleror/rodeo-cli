"""Plan values reach Ansible as `!unsafe` strings so Ansible never templates them.

Ansible templates variable values when it uses them, as root on the
controller. Without `!unsafe`, a plan value such as
"{{ lookup('pipe', 'touch /tmp/pwned') }}" (kept literal through rodeo's own
render with {% raw %}) runs inside ansible-playbook with no trust prompt.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from rodeo.engine.runner import DeployRunner, dump_ansible_vars, load_vars_file

PAYLOAD_TMPL = "{{{{ lookup('pipe', 'touch {marker}') }}}}"


def _cfg(dns_domain: str) -> dict:
    return {
        "name": "unsafe",
        "credentials": {
            "harvester_os_password": "Secret123",
            "harvester_token": "tok-unsafe",
        },
        "network": {"vip": "192.168.122.10", "dns_domain": dns_domain},
    }


def _vars_file(tmp_path, dns_domain: str):
    return DeployRunner(_cfg(dns_domain), tmp_path)._write_vars_file()


def test_every_string_value_is_tagged_unsafe(tmp_path):
    text = _vars_file(tmp_path, "rodeo.lab").read_text()
    data = yaml.load(text, Loader=yaml.BaseLoader)  # tags ignored, structure only
    assert "lab_dns_domain: !unsafe 'rodeo.lab'" in text
    assert "harvester_os_password: !unsafe 'Secret123'" in text
    # Every scalar string line in the file carries the tag; numbers and bools do not.
    for line in text.splitlines():
        if ":" not in line or line.rstrip().endswith(":"):
            continue
        value = line.split(":", 1)[1].strip()
        if value in ("true", "false", "[]", "{}", "''") or value.lstrip("-").isdigit():
            continue
        assert value.startswith("!unsafe"), line
    assert data["lab_dns_domain"] == "rodeo.lab"


def test_lookup_payload_is_emitted_unsafe(tmp_path):
    payload = PAYLOAD_TMPL.format(marker=tmp_path / "pwned")
    vars_file = _vars_file(tmp_path, payload)
    text = vars_file.read_text()
    line = next(ln for ln in text.splitlines() if ln.startswith("lab_dns_domain:"))
    assert line.startswith("lab_dns_domain: !unsafe ")
    assert load_vars_file(vars_file)["lab_dns_domain"] == payload


def test_round_trip_keeps_types(tmp_path):
    data = {"s": "x", "e": "", "n": 3, "b": True, "nested": {"l": ["a", 1], "s": "{{ y }}"}}
    path = tmp_path / "v.yaml"
    with open(path, "w") as f:
        dump_ansible_vars(data, f)
    assert load_vars_file(path) == data
    text = path.read_text()
    assert "n: 3" in text and "b: true" in text and "- 1" in text
    # Ansible reads `!unsafe ''` as null, so empty strings stay plain.
    assert "e: ''" in text
    assert "!unsafe '{{ y }}'" in text


def test_ansible_loader_reads_values_as_unsafe(tmp_path):
    """Ansible's own YAML loader sees the tag (skipped without ansible-core)."""
    loader_mod = pytest.importorskip("ansible.parsing.yaml.loader")
    payload = PAYLOAD_TMPL.format(marker=tmp_path / "pwned")
    vars_file = _vars_file(tmp_path, payload)
    data = loader_mod.AnsibleLoader(vars_file.read_text()).get_single_data()
    value = data["lab_dns_domain"]
    assert value == payload
    try:  # ansible-core < 2.19
        from ansible.utils.unsafe_proxy import AnsibleUnsafe
        is_unsafe = isinstance(value, AnsibleUnsafe)
    except ImportError:
        is_unsafe = False
    if not is_unsafe:  # ansible-core >= 2.19: unsafe means "not trusted as template"
        from ansible.module_utils._internal._datatag import AnsibleTagHelper
        from ansible._internal._datatag._tags import TrustedAsTemplate
        is_unsafe = TrustedAsTemplate not in AnsibleTagHelper.tag_types(value)
    assert is_unsafe


@pytest.mark.skipif(shutil.which("ansible-playbook") is None, reason="ansible-playbook not installed")
def test_playbook_does_not_run_lookup_from_plan_value(tmp_path):
    marker = tmp_path / "pwned"
    payload = PAYLOAD_TMPL.format(marker=marker)
    vars_file = _vars_file(tmp_path, payload)
    playbook = tmp_path / "pb.yml"
    playbook.write_text(
        "- hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: false\n"
        "  tasks:\n"
        "    - ansible.builtin.debug:\n"
        "        msg: \"domain={{ lab_dns_domain }}\"\n"
    )
    r = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(playbook), "-e", f"@{vars_file}"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert not marker.exists(), "Ansible templated a plan value: lookup executed"
    assert "lookup(" in r.stdout  # the literal string was printed, not run
