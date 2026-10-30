"""rodeo/labimport.py and rodeo new --from-lab-json: a lab-in-a-box lab as a rodeo profile.

The rule cases mirror tests/builder/logic.test.js (labWithSecretRefs / labWithNodeRefs)
so the CLI and the Rodeo Builder import a lab the same way.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from rodeo import labimport
from rodeo.commands.new_cmd import new_cmd
from rodeo.config import load_config
from rodeo.labinabox import build_lab_json

SMLM_PLAN = Path(__file__).parent.parent / "rodeo" / "data" / "examples" / "smlm-workshop" / "rodeo-plan.yaml"


def test_secret_values_become_placeholders():
    lab = {
        "common": {"ROOT_PWD_HASH": "$6$abc"},
        "nodes": {"a.lab": {"addons": [{"client_registration": {
            "client_registration_admin_pass": "s3cret", "client_registration_activation_key": "1-sles"}}]}},
        "kclusters": {"k1": {"clu_type": "rke2", "clu_token": "tok-one-1234567890"},
                      "k2": {"clu_type": "rke2", "clu_token": "tok-two-1234567890"}},
        "smlm": {"smlm_admin_pass": "??smlm_admin_pass", "smlm_scc_regcode": "REG-1", "smlm_ssl_password": "",
                 "smlm_users": [{"username": "a", "password": "s3cret"}, {"username": "b", "password": "other"}]},
    }
    out, replaced = labimport.with_secret_refs(lab, {"smlm_scc_regcode"})
    assert out["common"]["ROOT_PWD_HASH"] == "??ROOT_PWD_HASH"
    reg = out["nodes"]["a.lab"]["addons"][0]["client_registration"]
    assert reg["client_registration_admin_pass"] == "??client_registration_admin_pass"
    assert reg["client_registration_activation_key"] == "1-sles"
    assert out["kclusters"]["k1"]["clu_token"] == "??clu_token"
    assert out["kclusters"]["k2"]["clu_token"] == "??clu_token_2"
    assert out["smlm"]["smlm_admin_pass"] == "??smlm_admin_pass"
    assert out["smlm"]["smlm_scc_regcode"] == "??smlm_scc_regcode_2"
    assert out["smlm"]["smlm_ssl_password"] == ""
    assert out["smlm"]["smlm_users"][0]["password"] == "??client_registration_admin_pass"
    assert out["smlm"]["smlm_users"][1]["password"] == "??smlm_users_password"
    assert replaced == ["ROOT_PWD_HASH", "client_registration_admin_pass", "clu_token", "clu_token_2",
                        "smlm_scc_regcode_2", "smlm_users_password"]
    assert "s3cret" not in json.dumps(out)


def test_literal_node_names_and_addresses_become_references():
    lab = {
        "common": {"mydomain": "rodeo.lab"},
        "nodes": {
            "smlm.rodeo.lab": {"myip": "192.168.122.20"},
            "c1.rodeo.lab": {"myip": "192.168.122.2", "addons": [
                {"client_registration": {"client_registration_server": "smlm.rodeo.lab"}}]},
        },
        "smlm": {"url": "https://smlm.rodeo.lab/pub", "ip": "192.168.122.20", "near": "192.168.122.201",
                 "gw": "192.168.122.1", "mail": "admin@rodeo.lab", "other": "db.rodeo.lab", "kept": "smlm.rodeo.lab"},
    }
    out, warnings = labimport.with_node_refs(lab, {"smlm": {"kept": "smlm.rodeo.lab"}})
    assert out["smlm"]["url"] == "https://${node_fqdn:smlm}/pub"
    assert out["smlm"]["ip"] == "${node_ip:smlm}"
    assert out["smlm"]["near"] == "192.168.122.201"
    assert out["smlm"]["mail"] == "admin@rodeo.lab"
    assert out["smlm"]["kept"] == "smlm.rodeo.lab"
    assert out["nodes"]["c1.rodeo.lab"]["addons"][0]["client_registration"]["client_registration_server"] \
        == "${node_fqdn:smlm}"
    assert out["nodes"]["c1.rodeo.lab"]["myip"] == "192.168.122.2"
    assert warnings == [
        "smlm.near: 192.168.122.201 is in the lab's network but no node has it",
        "smlm.gw: 192.168.122.1 is in the lab's network but no node has it",
        "smlm.other: db.rodeo.lab is in the lab's domain but no node has that name",
    ]


def test_harvester_and_pxe_are_flagged():
    lab = {"nodes": {"h1.lab": {"kcluster": "hv"}, "h2.lab": {"kcluster": "hv"}, "r.lab": {"kcluster": "k"}},
           "kclusters": {"hv": {"clu_type": "harvester"}, "k": {"clu_type": "rke2"}}, "pxe": {}}
    warnings = labimport.drive_warnings(lab)
    assert warnings[0].startswith("Harvester nodes (h1.lab, h2.lab)")
    assert warnings[1].startswith("pxe:")
    assert labimport.drive_warnings({"nodes": {"a.lab": {}}}) == []


def test_read_lab_takes_json_or_yaml_and_refuses_others(tmp_path):
    (tmp_path / "lab.json").write_text('{"nodes": {"a.lab": {}}}')
    (tmp_path / "lab.yaml").write_text("nodes:\n  a.lab: {}\n")
    (tmp_path / "bad.yaml").write_text("common: {}\n")
    assert labimport.read_lab(tmp_path / "lab.json") == labimport.read_lab(tmp_path / "lab.yaml")
    with pytest.raises(ValueError, match="no nodes: map"):
        labimport.read_lab(tmp_path / "bad.yaml")


def test_new_from_lab_json_installs_a_rodeo_that_renders(tmp_path):
    lab = {"common": {"mydomain": "example.org"}, "nodes": {
        "srv.example.org": {"myip": "10.0.0.20", "VM_MEM": "8192", "addons": ["smlm"]},
        "cli.example.org": {"myip": "10.0.0.31", "addons": [{"client_registration": {
            "client_registration_server": "srv.example.org", "client_registration_activation_key": "1-x"}}]}},
        "smlm": {"smlm_admin_pass": "Literal-Pass-1", "smlm_admin_user": "admin"}}
    src = tmp_path / "lab.json"
    src.write_text(json.dumps(lab))
    result = CliRunner().invoke(new_cmd, ["mylab", "--from-lab-json", str(src)])
    assert result.exit_code == 0, result.output
    assert "smlm_admin_pass" in result.output and "Literal-Pass-1" not in result.output
    dest = tmp_path / ".rodeo" / "profiles" / "mylab"
    assert "Literal-Pass-1" not in (dest / "rodeo-plan.yaml").read_text()
    plan = yaml.safe_load((dest / "rodeo-plan.yaml").read_text())
    assert plan["operator_secrets"] == ["smlm_admin_pass"]
    stored = plan["lab_in_a_box"]["lab"]
    assert stored["smlm"]["smlm_admin_pass"] == "??smlm_admin_pass"
    reg = stored["nodes"]["cli.example.org"]["addons"][0]["client_registration"]
    assert reg["client_registration_server"] == "${node_fqdn:srv}"

    (tmp_path / ".rodeo" / "secrets.yaml").write_text(yaml.safe_dump({"universal_pwd": "u", "smlm_admin_pass": "p"}))
    cfg = load_config(dest / "rodeo-plan.yaml")
    assert sorted(cfg["vms"]) == ["cli", "srv"]
    rendered, _ = build_lab_json(cfg)
    assert sorted(rendered["nodes"]) == ["cli.rodeo.lab", "srv.rodeo.lab"]
    assert rendered["nodes"]["srv.rodeo.lab"]["myip"] == "192.168.122.20"
    assert rendered["nodes"]["srv.rodeo.lab"]["VM_MEM"] == "8192"
    assert rendered["nodes"]["cli.rodeo.lab"]["addons"][0]["client_registration"]["client_registration_server"] \
        == "srv.rodeo.lab"
    assert rendered["smlm"]["smlm_admin_pass"] == "p"

    again = CliRunner().invoke(new_cmd, ["mylab", "--from-lab-json", str(src)])
    assert again.exit_code == 1 and "already exists" in again.output
    mixed = CliRunner().invoke(new_cmd, ["other", "--from-lab-json", str(src), "--from", "harvester"])
    assert mixed.exit_code == 1 and "--from-lab-json" in mixed.output


def test_smlm_lab_imported_from_a_file_has_the_workshops_vms(tmp_path):
    smlm_lab = yaml.safe_load(SMLM_PLAN.read_text())["lab_in_a_box"]["lab"]
    src = tmp_path / "smlm.json"
    src.write_text(json.dumps(smlm_lab))
    result = CliRunner().invoke(new_cmd, ["smlm-copy", "--from-lab-json", str(src)])
    assert result.exit_code == 0, result.output
    plan = tmp_path / ".rodeo" / "profiles" / "smlm-copy" / "rodeo-plan.yaml"
    (tmp_path / ".rodeo" / "secrets.yaml").write_text("{}\n")
    cfg = load_config(plan)
    assert sorted(cfg["vms"]) == sorted(key.split(".")[0] for key in smlm_lab["nodes"])
    for key, node in smlm_lab["nodes"].items():
        assert cfg["vms"][key.split(".")[0]]["ip"] == node["myip"]


def test_schema_check_uses_the_installed_lab_in_a_box(tmp_path, monkeypatch):
    from rodeo import labinabox_host as host

    (tmp_path / "lab_schema").write_text("")
    monkeypatch.setattr(host, "LIAB_BIN", tmp_path)
    base = {"sections": {"common": {"fields": [{"name": "mydomain"}]}, "nodes": {"fields": [{"name": "myip"}, {"name": "addons"}]},
                         "kclusters": {"fields": []}}}
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "--base":
            return subprocess.CompletedProcess(cmd, 0, json.dumps(base), "")
        if cmd[1].endswith("install_smlm"):
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"fields": [{"name": "smlm_admin_pass"}]}), "")
        return subprocess.CompletedProcess(cmd, 1, "", "no such add-on")

    lab = {"common": {"mydomain": "x", "VM_ROOT": "y"}, "nodes": {"a.lab": {"myip": "1", "addons": ["smlm", "nope"]}},
           "smlm": {"smlm_admin_pass": "??p", "smlm_extra": 1}}
    unknown, where = labimport.schema_check(lab, run)
    assert unknown == ["common.VM_ROOT", "nodes.a.lab.addons.nope", "smlm.smlm_extra"]
    assert where == "installed in {}".format(tmp_path)
    assert labimport.schema_check(lab, lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "broken"))[0] is None


def test_schema_check_falls_back_to_a_fetched_or_local_checkout(tmp_path, monkeypatch):
    from rodeo import labinabox_host as host
    from rodeo.builder import discovery

    monkeypatch.setattr(host, "LIAB_BIN", tmp_path / "none")
    vendor = tmp_path / "vendor"
    for ver in ("1.9.0", "1.25.0", "1.10.2"):
        (vendor / ver / "webui").mkdir(parents=True)
    monkeypatch.setattr(host, "checkout_dir", lambda ref: vendor / ref)
    seen = []
    monkeypatch.setattr(discovery, "labinabox_schema", lambda checkout: seen.append(checkout.name) or
                        {"common": [], "nodes": ["myip"], "kclusters": [], "addons": {}})
    assert labimport.schema_check({"nodes": {"a": {"myip": "1", "x": 2}}}) == (["nodes.a.x"], "1.25.0")
    monkeypatch.setenv("RODEO_LABINABOX_PATH", str(tmp_path / "mine"))
    assert labimport.schema_check({"nodes": {}})[1] == "RODEO_LABINABOX_PATH" and seen == ["1.25.0", "mine"]
    monkeypatch.delenv("RODEO_LABINABOX_PATH")
    monkeypatch.setattr(host, "checkout_dir", lambda ref: tmp_path / "empty" / ref)
    assert labimport.schema_check({"nodes": {}}) == (None, "no lab-in-a-box is installed or fetched on this machine")


def test_new_from_lab_json_lists_the_fields_lab_in_a_box_lacks(tmp_path, monkeypatch):
    monkeypatch.setattr(labimport, "schema_check", lambda lab: (["nodes.a.lab.FOO"], "1.25.0"))
    src = tmp_path / "lab.json"
    src.write_text(json.dumps({"nodes": {"a.lab": {"myip": "10.0.0.2", "FOO": 1}}}))
    out = " ".join(CliRunner().invoke(new_cmd, ["one", "--from-lab-json", str(src)]).output.split())
    assert "nodes.a.lab.FOO: lab-in-a-box (1.25.0) doesn't know this field; rodeo up refuses it" in out
    monkeypatch.setattr(labimport, "schema_check", lambda lab: (None, "no lab-in-a-box is installed or fetched on this machine"))
    out = " ".join(CliRunner().invoke(new_cmd, ["two", "--from-lab-json", str(src)]).output.split())
    assert "Not checked against a lab-in-a-box schema (no lab-in-a-box is installed or fetched on this machine)" in out
