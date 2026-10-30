"""A lab-in-a-box lab (lab.json, or the YAML the lab-builder downloads) as a rodeo profile.

:func:`profile_files` turns a lab into the files of a lab-in-a-box rodeo: a
``rodeo-plan.yaml`` that carries the lab as ``lab_in_a_box.lab`` (deployed as
written, see :mod:`rodeo.labinabox`) and the lab-in-a-box platform's
``definition.yaml`` (the network; the VMs come from the lab). ``rodeo new
--from-lab-json`` installs them with :func:`rodeo.profile_install.install_profile`.

On the way in, the same rules as the Rodeo Builder's lab import (logic.js
``labWithSecretRefs`` / ``labWithNodeRefs``) apply:

- credential values (:data:`SECRET_KEY_RE`) become ``??<key>`` placeholders,
  listed in the plan's ``operator_secrets``; the values are dropped;
- literal node FQDNs and IPs in add-on values become ``${node_fqdn:<vm>}`` /
  ``${node_ip:<vm>}``, which rodeo fills per host and instance.

:func:`schema_check` then compares the lab with the schema of the lab-in-a-box
on this machine (installed, ``RODEO_LABINABOX_PATH``, or the newest one fetched
into ``~/.rodeo/vendor/``), the check deploy runs before building.
"""
from __future__ import annotations

import copy
import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from .profile_install import ProfileFile

LIAB_REPO = "https://github.com/SUSE-Technical-Marketing/lab-in-a-box"
LIAB_IMAGE = "openSUSE-Leap-15.6-Minimal-VM.x86_64-Cloud.qcow2"
LIAB_IMAGE_URL = "https://download.opensuse.org/distribution/leap/15.6/appliances/" + LIAB_IMAGE
PLATFORM_DEFINITION = Path(__file__).parent / "data" / "platforms" / "lab-in-a-box" / "definition.yaml"

# Lab keys whose values are credentials: passwords, tokens, registration codes, API and secret keys.
SECRET_KEY_RE = re.compile(r"(^|_)(pass|password|passwd|pwd|pwd_hash|token|regcode|secret|secret_key|private_key|api_keys?)$",
                           re.IGNORECASE)
_SECRET_REF_RE = re.compile(r"^\?\?([A-Za-z0-9_]+)$")
_LAB_OWN_KEYS = {"common", "nodes", "kclusters", "schema_versions"}
_IP_RE = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?!\d|\.\d)")


@dataclass
class LabImport:
    """A lab ready for a plan: placeholders and node references in, and what changed."""
    lab: dict
    replaced: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def read_lab(path: Path) -> dict:
    """The lab in *path* (JSON or YAML). Raises ValueError when it has no ``nodes:`` map."""
    text = path.read_text()
    try:
        lab = json.loads(text)
    except ValueError:
        try:
            lab = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"{path.name}: neither JSON nor YAML ({exc})") from None
    if not isinstance(lab, dict) or not isinstance(lab.get("nodes"), dict) or not lab["nodes"]:
        raise ValueError(f"{path.name}: not a lab-in-a-box lab (no nodes: map)")
    return lab


def secret_refs(value: Any) -> set[str]:
    """Keys of every ``"??<key>"`` value in *value*."""
    if isinstance(value, dict):
        return set().union(*(secret_refs(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(secret_refs(v) for v in value))
    match = _SECRET_REF_RE.match(value) if isinstance(value, str) else None
    return {match.group(1)} if match else set()


def with_secret_refs(lab: dict, taken: set[str] | None = None) -> tuple[dict, list[str]]:
    """*lab* with every non-empty credential value as a ``"??<key>"`` placeholder, and the new keys.

    The key is the field name; a field of the records of a list field is prefixed
    with that field (``smlm_users[].password`` → ``smlm_users_password``). Equal
    values share one key; another value under a taken name gets ``_2``, ``_3``, …
    Names in *taken* and the lab's own ``??`` keys are never reused.
    """
    used = set(taken or ()) | secret_refs(lab)
    by_value: dict[str, str] = {}
    replaced: list[str] = []

    def key_for(name: str, value: str) -> str:
        if value in by_value:
            return by_value[value]
        base = re.sub(r"[^A-Za-z0-9_]", "_", name)
        key, n = base, 2
        while key in used:
            key, n = f"{base}_{n}", n + 1
        used.add(key)
        by_value[value] = key
        replaced.append(key)
        return key

    def walk(value: Any, prefix: str) -> Any:
        if isinstance(value, list):
            return [walk(v, prefix) for v in value]
        if not isinstance(value, dict):
            return value
        out = {}
        for k, v in value.items():
            if (SECRET_KEY_RE.search(str(k)) and v is not None and not isinstance(v, (dict, list))
                    and v != "" and not str(v).startswith("??")):
                out[k] = "??" + key_for(f"{prefix}_{k}" if prefix else str(k), _scalar(v))
            else:
                out[k] = walk(v, str(k) if isinstance(v, list) and k != "addons" else "")
        return out

    return walk(lab, ""), replaced


def with_node_refs(lab: dict, before: dict | None = None) -> tuple[dict, list[str]]:
    """*lab* with literal node FQDNs and IPs in its add-on values as node references, and warnings.

    Add-on values are the add-on sections and the ``addons`` of nodes and
    kclusters; host keys (common, myip, node names) are left alone. A value
    equal to the one at the same place in *before* (the lab stored until now)
    is kept. Warnings name values that look like a lab address (an IP in a
    node's /24, a name in the lab's domain, not after ``@``) but match no node.
    """
    nodes = lab.get("nodes") or {}
    warnings: list[str] = []
    if not nodes:
        return lab, warnings
    names = sorted(nodes, key=len, reverse=True)
    ips = {str(n["myip"]): fqdn.split(".")[0] for fqdn, n in nodes.items() if isinstance(n, dict) and n.get("myip")}
    nets = {ip.rsplit(".", 1)[0] for ip in ips}
    domain = (lab.get("common") or {}).get("mydomain") or names[0].split(".", 1)[-1]
    fqdn_re = re.compile(r"(?<![\w.@-])(" + "|".join(map(re.escape, names)) + r")(?![\w-])")
    ip_re = re.compile(r"(?<![\d.])(" + "|".join(map(re.escape, ips)) + r")(?!\d|\.\d)") if ips else None
    host_re = re.compile(r"(?<![\w.@-])[A-Za-z0-9][\w-]*(?:\.[\w-]+)*\." + re.escape(domain) + r"(?![\w-])") if domain else None

    def fill(text: str, path: str) -> str:
        out = fqdn_re.sub(lambda m: "${node_fqdn:" + m.group(1).split(".")[0] + "}", text)
        if ip_re:
            out = ip_re.sub(lambda m: "${node_ip:" + ips[m.group(1)] + "}", out)
        warnings.extend(f"{path}: {m} is in the lab's network but no node has it"
                        for m in _IP_RE.findall(out) if m.rsplit(".", 1)[0] in nets)
        if host_re:
            warnings.extend(f"{path}: {m.group(0)} is in the lab's domain but no node has that name"
                            for m in host_re.finditer(out))
        return out

    def walk(value: Any, old: Any, path: str) -> Any:
        if isinstance(value, str):
            return value if value == old else fill(value, path)
        if isinstance(value, list):
            olds = old if isinstance(old, list) else []
            return [walk(v, olds[i] if i < len(olds) else None, f"{path}[{i}]") for i, v in enumerate(value)]
        if not isinstance(value, dict):
            return value
        prev = old if isinstance(old, dict) else {}
        return {k: walk(v, prev.get(k), f"{path}.{k}" if path else str(k)) for k, v in value.items()}

    prev = before if isinstance(before, dict) else {}
    out: dict[str, Any] = {}
    for key, value in lab.items():
        if key not in _LAB_OWN_KEYS:
            out[key] = walk(value, prev.get(key), key)
        elif key in ("nodes", "kclusters") and isinstance(value, dict):
            entries = {}
            for name, entry in value.items():
                if isinstance(entry, dict) and "addons" in entry:
                    old = ((prev.get(key) or {}).get(name) or {}).get("addons")
                    entry = {**entry, "addons": walk(entry["addons"], old, f"{key}.{name}.addons")}
                entries[name] = entry
            out[key] = entries
        else:
            out[key] = value
    return out, warnings


def drive_warnings(lab: dict) -> list[str]:
    """Parts of *lab* rodeo deploys but does not fully drive (lab-in-a-box still builds them)."""
    out = []
    harvester = {name for name, c in (lab.get("kclusters") or {}).items()
                 if isinstance(c, dict) and c.get("clu_type") == "harvester"}
    members = sorted(fqdn for fqdn, n in (lab.get("nodes") or {}).items()
                     if isinstance(n, dict) and n.get("kcluster") in harvester)
    if members:
        out.append("Harvester nodes (" + ", ".join(members) + "): rodeo ssh and rodeo status treat them as plain VMs")
    if "pxe" in lab:
        out.append("pxe: nodes that boot from PXE have no OS until lab-in-a-box installs them, so they "
                   "are not reachable at first")
    return out


def import_lab(lab: dict, before: dict | None = None, taken: set[str] | None = None) -> LabImport:
    """*lab* with secret placeholders and node references, the replaced keys and every warning."""
    masked, replaced = with_secret_refs(copy.deepcopy(lab), taken)
    linked, warnings = with_node_refs(masked, before)
    return LabImport(lab=linked, replaced=replaced, warnings=warnings + drive_warnings(linked))


def plan_text(name: str, imported: LabImport, target: str = "baremetal") -> str:
    """rodeo-plan.yaml for a lab-in-a-box rodeo that deploys *imported*'s lab as written."""
    plan = {
        "type": "lab-in-a-box",
        "name": name,
        "deployment_target": target,
        "resources": {"vm": {"memory_mib": 4096, "vcpu": 2, "disk_gb": 40}},
        "lab_in_a_box": {
            "source": {"repo": LIAB_REPO, "ref": "latest"},
            "root_password": "??universal_pwd",
            "iso_image": LIAB_IMAGE,
            "images": [{"name": LIAB_IMAGE, "url": LIAB_IMAGE_URL, "sha256_url": LIAB_IMAGE_URL + ".sha256"}],
            "lab": imported.lab,
        },
    }
    if imported.replaced:
        plan["operator_secrets"] = list(imported.replaced)
    header = ("# rodeo-plan.yaml — a lab-in-a-box lab imported with rodeo new --from-lab-json.\n"
              "# lab_in_a_box.lab is deployed as written, with this host's network, addresses and secrets.\n")
    return header + yaml.safe_dump(plan, sort_keys=False, default_flow_style=False, allow_unicode=True)


def profile_files(name: str, lab: dict, target: str = "baremetal") -> tuple[list[ProfileFile], LabImport]:
    """The files of a rodeo for *lab* (plan and definition) and the import's result."""
    imported = import_lab(lab)
    files = [ProfileFile("rodeo-plan.yaml", plan_text(name, imported, target).encode()),
             ProfileFile("definition.yaml", PLATFORM_DEFINITION.read_bytes())]
    return files, imported


def schema_check(lab: dict, run: Callable[..., Any] = subprocess.run) -> tuple[list[str] | None, str]:
    """(fields of *lab* the local lab-in-a-box schema doesn't declare, which lab-in-a-box that is);
    the fields are None when there is no lab-in-a-box here or its schema can't be read, and the
    second item then says why."""
    from . import labinabox_host as host
    from .labinabox import unknown_fields

    if (host.LIAB_BIN / "lab_schema").is_file():
        outputs: dict[str, str] = {}
        for name, cmd in host.schema_commands(host.addons_used(lab)).items():
            r = run(shlex.split(cmd), capture_output=True, text=True)
            if r.returncode == 0:
                outputs[name] = r.stdout
            elif name == "_base":
                return None, f"the installed lab-in-a-box has no base schema ({r.stderr.strip()[:160]})"
        try:
            return unknown_fields(lab, host.schema_from_outputs(outputs)), f"installed in {host.LIAB_BIN}"
        except (ValueError, KeyError) as exc:
            return None, f"could not read the installed lab-in-a-box schema: {exc}"
    checkout, where = host.local_override(), "RODEO_LABINABOX_PATH"
    if checkout is None:
        fetched = sorted((p for p in host.checkout_dir("latest").parent.glob("*") if (p / "webui").is_dir()),
                         key=lambda p: [int(n) if n.isdigit() else n for n in re.split(r"(\d+)", p.name)])
        checkout, where = (fetched[-1], fetched[-1].name) if fetched else (None, "")
    if checkout is None:
        return None, "no lab-in-a-box is installed or fetched on this machine"
    from .builder.discovery import labinabox_schema

    try:
        return unknown_fields(lab, labinabox_schema(checkout)), where
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        return None, str(exc)


def _scalar(value: Any) -> str:
    """*value* as the string a JSON reader would show (true/false, not True/False)."""
    return json.dumps(value) if isinstance(value, bool) else str(value)
