"""rodeo generate — interactive generator for definition + full config-dir skeleton.

Uses harvester-lab-config as base template (rails for valid Harvester rodeo).
Prompts (hybrid basic/advanced) to customize, generates yaml structure, copies artifacts dirs.
Post: validates, suggests next steps (cd dir; rodeo init --force; etc).

Supports future types via --type, but current focus Harvester (minimal 2-node or full 3+1).

Part of making first-phase zero manual after install.
"""

import os
import shutil
import tempfile
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.prompt import Confirm, Prompt

from ..config import load_config  # for post validate
from ._options import config_options

console = Console()

BASE_TEMPLATE = Path(__file__).parent.parent / "data" / "examples" / "harvester-lab-config"


def _prompt_basic():
    answers = {}
    answers["name"] = Prompt.ask("Lab name", default="my-harvester-lab")
    answers["num_harvester"] = int(Prompt.ask("Num Harvester nodes (2 for test/minimal, 3 for full)", default="2", choices=["2", "3"]))
    answers["include_rancher"] = Confirm.ask("Include Rancher?", default=(answers["num_harvester"] == 3))
    answers["deployment_target"] = Prompt.ask("Deployment target", default="baremetal", choices=["baremetal", "instruqt"])
    answers["storage_device"] = Prompt.ask("Storage device (e.g. /dev/nvme1n1 for multi-disk, empty for default)", default="")
    return answers


def _prompt_advanced(answers):
    console.print("[bold]Advanced mode:[/bold]")
    answers["harvester_vcpu"] = int(Prompt.ask("Harvester vCPU per node", default="6"))
    answers["harvester_mem"] = int(Prompt.ask("Harvester memory MiB per node", default="8192"))
    answers["harvester_disk"] = int(Prompt.ask("Harvester disk GB per node", default="100"))
    if answers.get("include_rancher"):
        answers["rancher_vcpu"] = int(Prompt.ask("Rancher vCPU", default="2"))
        answers["rancher_mem"] = int(Prompt.ask("Rancher memory MiB", default="4096"))
        answers["rancher_disk"] = int(Prompt.ask("Rancher disk GB", default="40"))
    answers["network_cidr"] = Prompt.ask("Network CIDR", default="192.168.122.0/24")
    answers["vip"] = Prompt.ask("VIP", default="192.168.122.10")
    return answers


def _customize_definition(def_path, answers):
    with open(def_path) as f:
        data = yaml.safe_load(f) or {}
    defn = data.get("definition", data)

    defn["name"] = answers["name"]
    defn["description"] = f"{answers['num_harvester']}-node Harvester HCI cluster{' + Rancher' if answers.get('include_rancher') else ''} on nested KVM (generated)"

    # nodes and lists
    nodes = []
    harvester_names = []
    for i in range(1, answers["num_harvester"] + 1):
        node_name = f"harvester{i}"
        harvester_names.append(node_name)
        # base from template or minimal
        base_node = {
            "name": node_name,
            "template": "harvester",
            "index": i,
            "hostname": f"{'alpha' if i==1 else 'bravo' if i==2 else 'charlie' if i==3 else f'node{i}'}",
            "ip": f"192.168.122.{10 + i}",
            "config_iso_name": f"harvester-config-node{i}",
            "ssh_user": "rancher",
            "infra_type": "harvester",
        }
        nodes.append(base_node)

    if answers.get("include_rancher"):
        nodes.append({
            "name": "rancher",
            "template": "rancher",
            "index": 0,
            "hostname": "rancher",
            "ip": "192.168.122.9",
            "config_iso_name": "",
            "ssh_user": "root",
            "infra_type": "rancher",
        })

    defn["nodes"] = nodes
    defn["start_order"] = harvester_names + (["rancher"] if answers.get("include_rancher") else [])
    defn["harvester_node_names"] = harvester_names
    defn["harvester_ready_count"] = answers["num_harvester"]

    # storage
    if answers.get("storage_device"):
        if "storage" not in defn:
            defn["storage"] = {}
        defn["storage"]["device"] = answers["storage_device"]

    # components: adjust for rancher
    if "components" in defn:
        comps = [c for c in defn["components"] if c["name"] != "rancher"]
        if not answers.get("include_rancher"):
            defn["components"] = comps
        else:
            if not any(c["name"] == "rancher" for c in comps):
                comps.append({"name": "rancher", "description": "Rancher Prime on K3s", "nodes": ["rancher"], "exposed": ["rancher"]})
            defn["components"] = comps

    # exposed: remove rancher if not
    if "exposed_services" in defn:
        exps = [e for e in defn["exposed_services"] if e["name"] != "rancher" or answers.get("include_rancher")]
        defn["exposed_services"] = exps

    with open(def_path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def _customize_plan(plan_path, answers):
    with open(plan_path) as f:
        data = yaml.safe_load(f) or {}

    data["name"] = answers["name"]
    data["deployment_target"] = answers["deployment_target"]

    if "resources" not in data:
        data["resources"] = {}
    data["resources"]["harvester"] = {
        "memory_mib": answers.get("harvester_mem", 8192),
        "vcpu": answers.get("harvester_vcpu", 6),
        "disk_gb": answers.get("harvester_disk", 100),
    }
    if answers.get("include_rancher"):
        data["resources"]["rancher"] = {
            "memory_mib": answers.get("rancher_mem", 4096),
            "vcpu": answers.get("rancher_vcpu", 2),
            "disk_gb": answers.get("rancher_disk", 40),
        }

    if "network" not in data:
        data["network"] = {}
    if answers.get("vip"):
        data["network"]["vip"] = answers["vip"]

    if answers.get("storage_device"):
        if "storage" not in data:
            data["storage"] = {}
        data["storage"]["device"] = answers["storage_device"]

    # credentials to ??env
    data["credentials"] = {
        "harvester_os_password": "??env:HARVESTER_OS_PASSWORD",
        "harvester_token": "??env:HARVESTER_TOKEN",
    }
    if answers.get("include_rancher"):
        data["credentials"]["lab_admin_password"] = "??env:LAB_ADMIN_PASSWORD"

    with open(plan_path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


@click.command("generate")
@click.option("--dir", "output_dir", default=".", help="Output directory for the generated config-dir skeleton (default: current dir)")
@click.option("--name", help="Lab name (will prompt if not given)")
@click.option("--advanced", is_flag=True, help="Ask advanced questions (resources, network, etc.)")
def generate_cmd(output_dir: str, name: str | None, advanced: bool):
    """Interactively generate a ready-to-use config-dir skeleton (definition.yaml + rodeo-plan.yaml + artifacts dirs) based on your answers.

    Uses harvester-lab-config as validated template base (avoids bad definitions).
    You can customize further after generation.
    """
    console.print("[bold]rodeo generate[/bold] — interactive definition + lab config generator\n")

    answers = _prompt_basic()
    if name:
        answers["name"] = name
    if advanced or Confirm.ask("Enter advanced mode for more customizations (resources etc)?", default=False):
        answers = _prompt_advanced(answers)

    out = Path(output_dir).resolve() / answers["name"]
    if out.exists():
        if not Confirm.ask(f"{out} exists. Overwrite?", default=False):
            console.print("[red]Aborted.[/red]")
            return
        shutil.rmtree(out)

    console.print(f"[bold]Copying template to {out}...[/bold]")
    shutil.copytree(BASE_TEMPLATE, out)

    # customize yamls
    def_file = out / "definition.yaml"
    plan_file = out / "rodeo-plan.yaml"
    _customize_definition(def_file, answers)
    _customize_plan(plan_file, answers)

    # post generate secrets like init (to make ready, use ??env)
    # simple random for now
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    pw = "".join(secrets.choice(alphabet) for _ in range(16))
    while not (any(c.isdigit() for c in pw) and any(c.isupper() for c in pw) and any(c.islower() for c in pw)):
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
    token = secrets.token_urlsafe(24)

    env_file = out / "rodeo-secrets.env"
    env_content = f'export HARVESTER_OS_PASSWORD="{pw}"\nexport LAB_ADMIN_PASSWORD="{pw}"\nexport HARVESTER_TOKEN="{token}"\n'
    env_file.write_text(env_content)

    # also create global secrets? but for generate, env is enough, user can init if wants global
    console.print(f"[green]Created {env_file}[/green] (use with source + sudo -E)")

    # validation
    try:
        cfg = load_config(None, config_dir=str(out))
        console.print("[green]✓ Generated files validated (load_config OK)[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠ Validation warning: {e}[/yellow]")

    console.print("\n[bold green]Generation complete![/bold green]")
    console.print(f"Lab config at: {out}")
    console.print("Next steps:")
    console.print(f"  cd {out}")
    console.print("  source rodeo-secrets.env")
    console.print("  rodeo plan --config-dir .")
    console.print("  sudo -E rodeo deploy --config-dir . --check")
    console.print("  # or rodeo bootstrap if in source tree")
    console.print("\nEdit the generated definition.yaml / rodeo-plan.yaml for further tweaks. infra_type is set for stop/start awareness.")
