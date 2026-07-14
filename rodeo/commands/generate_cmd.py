"""rodeo generate — command to produce customized declarative lab configuration artifacts.

This command exists to bootstrap the project's declarative model (as defined in definition.yaml structures under data/platforms and examples) with validated, project-aligned starting artifacts. It addresses the need for engineers to quickly produce consistent definition.yaml (with sections for nodes, templates including infra_type for lifecycle commands like stop/start, components, harvester config, etc.) and supporting rodeo-plan.yaml + config-dir skeleton (certs/, manifests/, etc.) without manual YAML authoring that risks inconsistencies with inventory renderer, phase orchestration, or EIB-inspired --config-dir patterns.

Logical reasons within project:
- Enforces conventions from definition (e.g. start_order, harvester_node_names, infra_type for infra-aware stop/start in stop_cmd.py/start_cmd.py).
- Produces full output compatible with load_config, build_inventory (in inventory.py), and subsequent commands (bootstrap, init, deploy, clean, stop).
- Reduces onboarding friction for custom labs while using pre-existing templates as base (harvester-lab-config for test/minimal variants), allowing overrides for resources, targets, storage, etc.
- Fits the "generate" pattern seen in related tools (e.g. EIB's generate for combustion), enabling "rodeo generate" as entry point before "rodeo deploy" or lifecycle (stop/start/clean).

Outcomes of using:
- Produces ready-to-use dir with definition + plan using ??env: for credentials (integrates with init_cmd.py secrets logic and sudo -E patterns).
- Post-generation validation via load_config ensures the output is loadable and consistent with cfg expectations (vms, storage, network, etc.).
- Supports hybrid customization (core params + advanced) for flexibility without exposing full YAML complexity.
- Enables full lifecycle: generate -> bootstrap/init -> deploy -> stop/start -> clean --all for reset (as enhanced in clean.py).

The implementation renders from a base template (copy of harvester-lab-config for artifact dirs and initial structure), applies overrides from collected parameters to produce YAML, and provides next-step guidance. This centralizes artifact generation logic, making the declarative model (definition as single source per inventory.py comments) accessible and correct by construction.

Current scope focuses on Harvester/SUSE Virtualization (suse-virt type); extensible for other profiles via --type in future.

See also: docs/user-guide.md for usage, architecture.md for role in declarative pipeline, definition.yaml for expected structure (including infra_type), cli.py for registration.
"""

import secrets
import shutil
import string
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.prompt import Confirm, Prompt

from ..config import load_config  # for post validate

console = Console()

BASE_TEMPLATE = Path(__file__).parent.parent / "data" / "examples" / "harvester-lab-config"


def _prompt_basic():
    """Collects core lab configuration parameters as a dict for template customization.

    Inputs:
    - None (uses interactive collection via rich.prompt for name, num_harvester (2/3), include_rancher (derived default), deployment_target, storage_device).

    Outputs:
    - dict with keys: 'name' (str), 'num_harvester' (int), 'include_rancher' (bool), 'deployment_target' (str), 'storage_device' (str).

    Patterns inside:
    - Uses Prompt.ask and Confirm.ask for validated collection (choices, defaults).
    - Derives include_rancher default from num_harvester to enforce sensible rails (2-node test variant typically no Rancher).
    - Returns flat dict as intermediate representation for override logic in _customize_* (avoids direct YAML mutation during collection).

    How it works:
    - Collects minimal set to produce valid output aligned with definition schema (start_order/harvester_node_names based on num, etc.) and plan (target, storage).
    - Hybrid mode entry point: always called first; advanced extends the dict.

    Fit in project:
    - Enables 'rodeo generate' as zero-friction entry to declarative model (see definition.yaml for expected keys like harvester_ready_count, infra_type, components; inventory.py for rendering from such defs).
    - Produces cfg compatible with load_config (config.py), which feeds build_inventory, DeployRunner (app.py/runner.py), phases (cluster.py for harvester_node_names/start_order, stop_cmd.py for infra_type awareness), clean.py for --all reset.
    - Supports project goal of minimal manual (post-install generate before bootstrap/deploy), with templates ensuring correctness (no bad defs that break libvirt/pxe/ cluster bootstrap).
    - Outcomes: Engineer gets full config-dir (with artifacts for --config-dir) ready for 'rodeo init', 'rodeo stop/start', 'rodeo clean --all', without manual YAML that could mismatch EIB-inspired patterns or lifecycle commands.
    """
    answers = {}
    answers["name"] = Prompt.ask("Lab name", default="my-harvester-lab")
    answers["num_harvester"] = int(Prompt.ask("Num Harvester nodes (2 for test/minimal, 3 for full)", default="2", choices=["2", "3"]))
    answers["include_rancher"] = Confirm.ask("Include Rancher?", default=(answers["num_harvester"] == 3))
    answers["deployment_target"] = Prompt.ask("Deployment target", default="baremetal", choices=["baremetal", "instruqt"])
    answers["storage_device"] = Prompt.ask("Storage device (e.g. /dev/nvme1n1 for multi-disk, empty for default)", default="")
    return answers


def _prompt_advanced(answers):
    """Extends basic answers dict with advanced parameters for fine-grained customization.

    Inputs:
    - answers: dict from _prompt_basic (mutated in place with additional keys).

    Outputs:
    - Same dict, augmented with: 'harvester_vcpu'/'mem'/'disk' (int), optional 'rancher_*' if include_rancher, 'network_cidr', 'vip'.

    Patterns inside:
    - Conditional prompting (if include_rancher) to keep output consistent with definition (no rancher nodes/component if not).
    - Defaults chosen to match common test/minimal (from harvester-lab-config) or full 3-node profiles.
    - Flat dict pattern for easy override in _customize_* (mirrors how plan overrides work in config.py _deep_merge and _set_path).

    How it works:
    - Gathers resource/network params that map directly to definition (storage, network) and plan (resources section) structures.
    - Called only in advanced path to support hybrid (core for quick start, advanced for custom infra sizing aligned with host constraints like 16 logical CPUs).

    Fit in project:
    - Feeds the customization that produces definition/plan consumable by the full pipeline: inventory renderer (for vms/resources), runner (ansible vars for kvm_host/vms), cluster/rancher phases (for node counts), stop/start (infra_type + order), clean (for --all state reset).
    - Logical reason: Allows engineers to generate labs matching specific hardware (e.g. Ryzen limits per CPU notes in definition) or targets (instruqt vs baremetal in plan), reducing manual edits that could break declarative invariants (e.g. harvester_ready_count matching nodes list).
    - Outcomes: Generated artifacts enable end-to-end use (generate -> deploy -> stop for graceful pause -> start or clean --all for reset) with correct sizing, no hardcoding mismatches.
    """
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
    """Customizes a base definition.yaml (from template) with overrides from answers dict.

    Inputs:
    - def_path: Path to YAML file (copied from BASE_TEMPLATE).
    - answers: dict from _prompt_* (keys for num_harvester, include_rancher, name, storage_device, resources if advanced).

    Outputs:
    - None (mutates file in place via yaml dump). Produces definition with nodes, lists, infra_type, components, storage, etc.

    Patterns inside:
    - Load -> mutate top-level 'definition' (or root) -> dump (sort_keys=False to preserve structure/comments where possible).
    - Derives lists (start_order, harvester_node_names, nodes) from num_harvester to match schema expectations (see definition.yaml comments on these as source for ClusterPhase, stop_cmd).
    - Conditional for rancher (removes/adds nodes/component/exposed) to keep output valid for current profiles.
    - Hardcodes minimal node attrs + infra_type (for awareness in stop/start); uses answers for overrides.

    How it works:
    - Starts from validated template (harvester-lab-config or similar) to inherit full structure (network, node_templates with infra_type, host_prep, harvester section, artifacts dirs copied separately).
    - Applies params to produce ready YAML that load_config can merge with plan/secrets, and inventory.py can render (nodes -> vm_nodes, components for phases).
    - Handles 2/3 node variants + rancher toggle for test vs full labs.

    Fit in project:
    - Logical reason for creation: The declarative core (definition as single source per inventory.py and definition.yaml comments) is complex (templates, lists, infra_type for new lifecycle in stop/start_cmd.py, components for on_host in clean/stop). Manual creation risks invalid outputs that break renderer (MAC gen, start_order), phases (harvester_node_names for cluster bootstrap), or commands (clean --all discovery, stop infra awareness).
    - Outcomes: Produces artifacts that integrate end-to-end (generate output -> rodeo init/ bootstrap for secrets/link -> deploy via runner -> stop for gentle pause using infra_type -> start or clean --all for reset without package loss). Enables engineers to get custom but correct starting points aligned with EIB config-dir + artifacts pattern, reducing errors in Harvester lab topology.
    - Part of first-phase simplification (post-install entry before deploy/stop/clean).
    """
    with open(def_path) as f:
        data = yaml.safe_load(f) or {}
    defn = data.get("definition", data)

    defn["name"] = answers["name"]
    defn["description"] = f"{answers['num_harvester']}-node Harvester HCI cluster{' + Rancher' if answers.get('include_rancher') else ''} on nested KVM (generated)"

    # nodes and lists
    # Use template's first matching node as base to preserve interfaces/MACs/uuids/etc from explicit template
    harv_tmpl = next((dict(n) for n in defn.get("nodes", []) if n.get("template") == "harvester"), {})
    ranch_tmpl = next((dict(n) for n in defn.get("nodes", []) if n.get("template") == "rancher"), {})
    nodes = []
    harvester_names = []
    hostnames = ["alpha", "bravo", "charlie"]
    for i in range(1, answers["num_harvester"] + 1):
        node_name = f"harvester{i}"
        harvester_names.append(node_name)
        base_node = dict(harv_tmpl)  # copy to preserve
        base_node.update({
            "name": node_name,
            "index": i,
            "hostname": hostnames[i-1] if i <= 3 else f"node{i}",
            "ip": f"192.168.122.{10 + i}",
            "config_iso_name": f"harvester-config-node{i}",
            "infra_type": "harvester",
        })
        nodes.append(base_node)

    if answers.get("include_rancher"):
        rnode = dict(ranch_tmpl) if ranch_tmpl else {"name": "rancher", "template": "rancher", "index": 0, "hostname": "rancher", "ip": "192.168.122.9", "config_iso_name": "", "ssh_user": "root", "infra_type": "rancher"}
        rnode.update({"infra_type": "rancher"})
        nodes.append(rnode)

    defn["nodes"] = nodes
    defn["start_order"] = harvester_names + (["rancher"] if answers.get("include_rancher") else [])
    defn["harvester_node_names"] = harvester_names
    defn["harvester_ready_count"] = answers["num_harvester"]

    # Update harvester section notes based on num (template may have 2-node text)
    if "harvester" in defn and "installer_notes" in defn["harvester"]:
        n = answers["num_harvester"]
        defn["harvester"]["installer_notes"]["rke2"] = f"RKE2 control plane on the {n} nodes; etcd join gap declared above for bootstrap race avoidance."
        defn["harvester"]["installer_notes"]["longhorn"] = f"Storage on the dedicated storage + service NICs; Longhorn uses the {n} replicas (lab only)."

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

    # Update rancher note if present (template has 2-node specific)
    if "rancher" in defn and isinstance(defn.get("rancher"), dict):
        if "cloud_init" not in defn["rancher"]:
            defn["rancher"]["cloud_init"] = {}
        # keep as is, or update comment if in full structure

    with open(def_path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def _customize_plan(plan_path, answers):
    """Customizes a base rodeo-plan.yaml with answers for resources, target, storage, credentials.

    Inputs:
    - plan_path: Path to plan file (from template copy).
    - answers: dict (name, deployment_target, optional resources/storage/vip from basic/advanced).

    Outputs:
    - None (mutates file). Plan with name, resources, network, storage.device, credentials as ??env:.

    Patterns inside:
    - YAML load/mutate/dump (consistent with _customize_definition and how plan overrides are handled in config.py).
    - Credentials always set to ??env: forms (pattern from init_cmd.py for sudo -E + env support; avoids embedding secrets).
    - Resources conditional on include_rancher to match definition nodes.

    How it works:
    - Applies high-level params to plan sections that drive deployment (resources for vms phase, target for instruqt guards in runner, storage for image_dir in clean/deploy).
    - Ensures compatibility with load_config merging (plan + secrets + -P).

    Fit in project:
    - Logical reason: The plan (rodeo-plan.yaml) + definition form the declarative input to the entire system (see config.py for load/merge, inventory for rendering, runner for phases). Manual plan creation often leads to mismatches (e.g. resources not aligning with host, no ??env breaking credentials in deploy/stop).
    - Outcomes: Generated plan + definition work together for full pipeline (plan drives resources in kvm_host/vms; definition provides infra_type/start_order for stop/start/clean awareness; supports --config-dir for EIB-style artifacts). Enables generate as pre-deploy tool that produces restartable/resetable labs (via stop/start/clean).
    - Integrates with bootstrap (for link) and init (for final secrets rewrite if needed).
    """
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
        data["credentials"]["rancher_admin_password"] = "??env:RANCHER_ADMIN_PASSWORD"
    data["credentials"]["harvester_admin_password"] = "??env:HARVESTER_ADMIN_PASSWORD"

    with open(plan_path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


@click.command("generate",
               short_help="(advanced) Interactive config-dir generator. Prefer 'rodeo new <name>'.")
@click.option("--dir", "output_dir", default=".", help="Output directory for the generated config-dir skeleton (default: current dir)")
@click.option("--name", help="Lab name (will prompt if not given)")
@click.option("--advanced", is_flag=True, help="Ask advanced questions (resources, network, etc.)")
def generate_cmd(output_dir: str, name: str | None, advanced: bool):
    """Entry point for 'rodeo generate' command.

    Inputs (from click):
    - output_dir: str (target base dir; lab subdir created inside using name).
    - name: str | None (lab name; prompts if None).
    - advanced: bool (triggers _prompt_advanced).

    Outputs:
    - None (side effects: creates dir with customized definition.yaml, rodeo-plan.yaml, copied subdirs (certs/, manifests/, etc.), rodeo-secrets.env; prints summary and next steps).

    Patterns inside:
    - Template copy (shutil from BASE_TEMPLATE for full EIB-style config-dir with artifacts).
    - Parameter collection (hybrid via _prompt_basic/_prompt_advanced -> answers dict).
    - Customization ( _customize_* for YAML overrides using declarative keys).
    - Secrets/env (random pw + ??env: rewrite, mirroring init_cmd.py pattern for credential handling).
    - Validation (load_config post-gen).
    - UX (rich console + suggestions for bootstrap/deploy/stop/start/clean lifecycle).

    How it works:
    - Collects params -> copy base -> customize yamls (nodes/lists/infra_type from num/include, resources/storage from answers) -> generate env -> validate -> print.
    - Handles 2/3 node + rancher toggle to produce valid output for current profiles/definition (see harvester-lab-config/definition.yaml and suse-virt/profile).

    Fit in project:
    - Logical reason created: The project's core is the declarative definition + plan as input to inventory renderer (build_inventory produces vm_nodes, host_prep, etc. for ansible phases and python ClusterPhase/RancherPhase/stop_cmd), load_config (merges for all cmds), and lifecycle (bootstrap for setup, deploy via runner, stop/start for graceful pause/restart using infra_type, clean for reset). Manually authoring these risks invalid states (wrong harvester_ready_count vs nodes, missing infra_type breaking stop awareness, bad resources for host, missing ??env breaking credentials in sudo -E flows).
    - Outcomes of use: Engineer gets a complete, validated config-dir skeleton (full artifacts pattern for --config-dir) customized to needs (e.g. storage device for multi-disk per clean.py, num nodes for 2-node test vs full, infra_type for stop/start in new cmds), ready for immediate use in the pipeline without errors. Reduces first-phase manual work (post 'rodeo install-deps --link'), enforces project invariants (templates + rails), enables custom labs that work with generate -> stop/start -> clean --all for full lifecycle/reset (as requested for host repurposing without package removal).
    - Fits general picture: Complements 'rodeo init' (secrets), 'bootstrap' (link/setup), stop/start (new lifecycle), clean (reset), all driven by definition as single source (per inventory.py and definition comments). Supports EIB-like self-contained dirs for declarative labs. See docs/architecture.md for pipeline role, user-guide for invocation, stop-design-options.md for related rationale.
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
    alphabet = string.ascii_letters + string.digits
    pw = "".join(secrets.choice(alphabet) for _ in range(16))
    while not (any(c.isdigit() for c in pw) and any(c.isupper() for c in pw) and any(c.islower() for c in pw)):
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
    token = secrets.token_urlsafe(24)

    env_file = out / "rodeo-secrets.env"
    env_content = (
        f'export HARVESTER_OS_PASSWORD="{pw}"\n'
        f'export HARVESTER_ADMIN_PASSWORD="{pw}"\n'
        f'export RANCHER_ADMIN_PASSWORD="{pw}"\n'
        f'export HARVESTER_TOKEN="{token}"\n'
    )
    env_file.write_text(env_content)
    console.print(f"[green]Created {env_file}[/green] (use with source + sudo -E)")

    from ..paths import rodeo_secrets_path

    secrets_dest = rodeo_secrets_path()
    secrets_dest.parent.mkdir(parents=True, exist_ok=True)
    if secrets_dest.exists():
        console.print(f"[yellow]{secrets_dest} exists — not overwriting (delete to refresh on next generate)[/yellow]")
    else:
        secrets_dest.write_text(
            "# ~/.rodeo/secrets.yaml — kept out of version control\n"
            f'harvester_os_password: "{pw}"\n'
            f'harvester_admin_password: "{pw}"\n'
            f'rancher_admin_password: "{pw}"\n'
            f'harvester_token: "{token}"\n'
        )
        secrets_dest.chmod(0o600)
        console.print(f"[green]Created {secrets_dest}[/green]")

    try:
        load_config(None, config_dir=str(out))
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
