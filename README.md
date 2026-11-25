<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo/wordmark-dark.png">
  <img alt="rodeo-cli" src="docs/assets/logo/wordmark-light.png" height="60">
</picture>

A CLI for deploying hands-on lab infrastructure. Point it at a Linux host with KVM, pick a profile, and it builds a working lab of nested VMs (Harvester HCI, Rancher Prime, SUSE Edge, SUSE Telco Cloud, or a plain KVM host) without you writing a line of Ansible or touching libvirt directly.

[![Release](https://img.shields.io/github/v/release/avaleror/rodeo-cli?sort=semver&label=release)](https://github.com/avaleror/rodeo-cli/releases)
[![CI](https://github.com/avaleror/rodeo-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/avaleror/rodeo-cli/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
[![License](https://img.shields.io/badge/license-GPL--3.0-green)](LICENSE)

**[📖 Docs & user guide](https://avaleror.github.io/rodeo-cli/)**

---

## How it works

A **profile** describes a lab: how many VMs, what they run, how much RAM, what ports to expose on the host. Two YAML files define each profile:

- `rodeo-plan.yaml`: resources, credentials, and where the lab runs (`baremetal`, `instruqt`, `aws`, `gcp`, or `remote`)
- `definition.yaml`: topology: nodes, network, exposed services, start order

The CLI reads those files and drives a **phase pipeline** through Ansible roles on the target host. Not every phase runs in every profile: the engine `type` decides which ones apply:

```
kvm_host → vms → [boot | pxe_server → cluster] → [rancher] → [elemental] → apply → finalise
```

`kvm_host` prepares the hypervisor (packages, libvirt, firewall, storage). `vms` creates disk images and VM definitions. Cloud-init labs (Rancher, SUSE Edge) then run `boot` to start the network and VMs directly; Harvester labs instead run `pxe_server` (nginx + TFTP + per-node iPXE scripts) and `cluster` (starts VMs, waits for Harvester to install via network boot). `rancher` installs Rancher Prime on K3s and configures the admin API. `elemental` (SUSE Edge only) installs the Elemental Operator so edge nodes can register over TPM. `apply` applies any extra manifests. `finalise` enables VM autostart.

Credentials live in `~/.rodeo/secrets.yaml` (chmod 600, never committed). The plan references them with `??key` placeholders; `rodeo up` generates that file for you.

---

## Quick start

One command to install on SLES 16 / Leap 16:

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

Then:

```bash
rodeo up
```

That is it. The installer installs the latest validated release (the one in [`STABLE`](STABLE), which passed a live deploy; `--ref main` for the latest code), sets up a Python environment internally, and links `rodeo` as a system command. No venv to activate, no PATH to set, no sudo prefix, ever.

On a laptop (macOS, or any other Linux, SLES 15 included) the same command installs rodeo as a control machine, without root, and you deploy to the cloud instead:

```bash
rodeo up --profile rancher --target aws
```

See [Install on Linux and macOS](docs/install.md).

To install from a fork, set `RODEO_REPO` (and optionally `RODEO_REF`) for the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/<you>/rodeo-cli/main/install.sh | RODEO_REPO=https://github.com/<you>/rodeo-cli.git bash
```

`rodeo up` self-escalates with sudo, auto-detects an existing lab dir, and ends with the URLs and credentials to log in. It also wraps itself in a **tmux session** automatically, so a dropped SSH or Instruqt connection does not kill a running deploy. Re-attach any time with `tmux attach -t rodeo-<profile>`.

Use `--no-tmux` to skip the tmux wrap in scripts.

To pick a specific profile:

```bash
rodeo up --profile rancher-test   # Rancher + single-node K3s and RKE2 clusters, ~18 GiB RAM
rodeo up --profile rancher        # Rancher + K3s, RKE2 and 3-node RKE2 clusters, ~30 GiB RAM
rodeo up --profile harvester-ha   # 3-node Harvester HA, ~52 GiB RAM
rodeo up --profile harvester      # full lab: 3-node Harvester + Rancher, ~72 GiB RAM
rodeo up --profile suse-edge      # SUSE Edge: Rancher + Elemental + EIB + edge nodes
```

---

## Profiles

| Profile | Engine type | What it deploys | RAM needed |
|---------|-------------|----------------|-----------|
| `rancher-test` | `rancher-test` | Rancher Prime on K3s + single-node K3s and RKE2 clusters (3 VMs) | ~18 GiB |
| `rancher` | `rancher` | Rancher Prime on K3s + single-node K3s, single-node RKE2 and 3-node RKE2 clusters (6 VMs) | ~30 GiB |
| `test` | `suse-virt` | 2-node Harvester cluster, no Rancher | ~36 GiB |
| `harvester-ha` | `suse-virt` | 3-node Harvester, no Rancher (3-member etcd HA) | ~52 GiB |
| `harvester-2n` | `suse-virt` | 2-node Harvester + Rancher Prime | ~56 GiB |
| `harvester` | `suse-virt` | 3-node Harvester HCI + Rancher Prime | ~72 GiB |
| `virt-workshop` | `suse-virt` | `harvester` plus image cache, NFS backup target and sample VMs for the [SUSE Virtualization workshop](https://avaleror.github.io/suse-virt-workshop/) | AWS `m8id.8xlarge` |
| `virt-workshop-2n` | `suse-virt` | 2-node workshop, same scripts, budget tier | AWS `m8id.4xlarge` |
| `suse-edge` | `suse-edge` | Rancher + Elemental + EIB + edge nodes (SUSE Edge 3.7) | ~40 GiB |
| `suse-telco` | `suse-telco` | SUSE Telco Cloud 3.7: management cluster, emulated Redfish BMC, downstream image, hosts enrolled. Students apply the workshop manifests | see the [telco guide](docs/guide-suse-telco.md) |
| `smlm-workshop` | `lab-in-a-box` | SUSE Multi-Linux Manager + 8 clients. rodeo prepares the host; lab-in-a-box builds the VMs | ~30 GiB |
| `kvm-hosts` | `kvm-hosts` | KVM and libvirt only. You create the VMs. Fleet can turn the dead-man switch off | 8 vCPU / 32 GiB per host on AWS |

`rodeo doctor` recommends the largest profile that fits available RAM. `rodeo profiles` lists all profiles including any you create yourself. The engine (`suse-virt`, `rancher`, `rancher-test`, `suse-edge`, `suse-telco`, `kvm-hosts`, `lab-in-a-box`) decides which phases run. Where the lab runs is `--target`, not a second profile: the old `virt-workshop-aws` names still work and mean `virt-workshop`.

You can scaffold and customize your own:

```bash
rodeo new mylab --from harvester
$EDITOR ~/.rodeo/profiles/mylab/definition.yaml
rodeo up --profile mylab
```

Full walkthrough: [Create your own rodeo](docs/custom-rodeos.md).

### Where it runs

| `--target` | What happens |
|---|---|
| `baremetal` | Deploy on this machine. The default. |
| `instruqt` | Deploy on an Instruqt builder. `finalise` is skipped so a host image stays bootable. |
| `aws` | From a laptop: create an EC2 host, then deploy over SSH. Region `eu-north-1` or `$RODEO_AWS_REGION`. |
| `gcp` | From a laptop: create a Compute Engine VM, then deploy over SSH. Region `europe-west4` or `$RODEO_GCP_REGION`. Live-tested for a single host. |
| `remote` | Deploy over SSH to a KVM host you already own. rodeo does not create it and does not destroy it. |

```bash
rodeo up --profile harvester --target aws
rodeo up --profile harvester --target gcp
```

Every cloud host has a **6-hour dead-man switch**: it terminates itself unless you set `provider.ttl_hours` (168 hours maximum). `kvm-hosts` fleets can turn it off with `--no-deadman`. Tear a cloud host down sooner with `rodeo destroy --cloud --yes`.

A workshop fleet (`rodeo fleet`) can provision many AWS or GCP hosts. The student claim portal and `fleet open-access` are AWS-only. Vultr and Hetzner are named in the fleet reference, and the provider registry rejects them. See [Get started](docs/get-started.md) and [Fleet](docs/fleet.md).

---

## Commands

| Command | What it does |
|---------|-------------|
| `up` | Front door: host check, install deps, pick lab, generate secrets, deploy, print login info. Wraps in tmux automatically: re-attach with `tmux attach -t rodeo-<profile>` |
| `doctor` | Host readiness check and profile recommendation by available RAM |
| `new` | Scaffold a custom lab from a bundled base: `rodeo new mylab --from harvester` · install a Rodeo Builder download: `rodeo new mylab --from-zip mylab.zip` · or a published rodeo: `rodeo new mylab --from https://github.com/<owner>/<repo>` |
| `builder` | Rodeo Builder web UI, live: compose a rodeo and save it into your profiles (`rodeo builder`) |
| `profiles` | List deployable profiles (bundled, your custom ones in `~/.rodeo/profiles/`, and the published rodeos of the catalog) |
| `list` | Show plans and the libvirt domains stamped for each one |
| `install-deps` | Install host packages (KVM, libvirt, ansible, kubectl). `--story` adds rmstory + multilang from their GitHub release distro packages (refused without a matching `.sha512`); `--aws` adds the AWS CLI v2 (signature-checked) for labs whose VMs run in AWS |
| `init` | Create `rodeo-plan.yaml` and `~/.rodeo/secrets.yaml` |
| `plan` | Preview what deploy would change (no changes made) |
| `instances` | Several copies of a lab-in-a-box lab on one host: `instances new <profile> --count N`, `instances list` |
| `export` | Render the lab spec for another deployer: currently lab-in-a-box `lab.json` (`type: lab-in-a-box` labs deploy through it directly) |
| `story` | Render the workshop hand-out (`story render`): languages and story variants via rmstory, topology facts filled in |
| `content` | The lab's instructions apart from its infrastructure: `content apply` fetches the plan's `content:` and renders `<lab>/guide/` without redeploying |
| `deploy` | Run the phase pipeline. Flags: `--from PHASE`, `--force`, `--reconcile` (default on) / `--no-reconcile`, `--check`, `--no-tui`, `-P key=value` |
| `status` | VM states, VIP reachability, phase progress |
| `stop` | Graceful stop in reverse definition order (infra-aware) |
| `start` | Start host services and VMs in definition order |
| `start-if-needed` | Idempotent boot guard: start only what is stopped |
| `clean` | Destroy lab VMs, disks, state. `--all --yes --secrets` for a full host reset |
| `destroy` | Terminate the cloud host of a lab (`--cloud`) and its security group |
| `fleet` | Multi-host workshops: `provision`, `deploy`, `status`, `doctor`, `retry`, `diagnose`, `access`, `open-access`, `portal` (student claim portal), `deprovision` |
| `mcp` | Serve rodeo to MCP clients (agents, editors) over stdio. Read-only unless `--allow-write`; covers native and external engines. See [MCP server](docs/mcp.md) |
| `watch` | Split-panel TUI: phase progress + VM serial logs |
| `ssh` | SSH into a lab VM: `rodeo ssh harvester1` |
| `logs` | Tail VM serial log. `--bundle` packages a support tarball |
| `restart` | Restart a single VM |
| `attach` | Serial console (Ctrl+] to detach) |
| `set-password` | Rotate the Harvester / Rancher Prime admin password on a running lab |
| `install-extensions` | Install Rancher UI extensions (e.g. Harvester) |
| `pull-edge-image` | (SUSE Edge) Seed edge node boot disks from the EIB VM or a local image |
| `eject-iso` | (SUSE Edge) Remove the install ISO from edge nodes and boot from disk |
| `self-update` | Move to the latest validated release (`--branch main` for the latest code) and reinstall the CLI in one shot, then restart the Rodeo Builder service on a host that runs it (`--no-restart` to skip); refuses to discard local changes without `--force` |
| `bootstrap` | (advanced) One-shot host setup for clean SLES, links binary, seeds a lab dir |
| `generate` | (advanced) Interactive config-dir skeleton from templates |

---

## Configuration

Plans go in the lab directory (`rodeo-plan.yaml`). Secrets go in `~/.rodeo/secrets.yaml`. The CLI walks up from the current directory to find a lab, so you can run commands from inside it without `--config-dir`.

Override plan values at deploy time without editing files:

```bash
rodeo deploy -P resources.harvester.memory_mib=20480
rodeo deploy --paramfile overrides.yaml
```

Precedence: profile defaults < plan < paramfile < `-P`.

---

## Documentation

**Deployment guides**

| Guide | For |
|-------|-----|
| [Rancher profile guide](docs/guide-rancher.md) | Deploy Rancher Prime with K3s and RKE2 downstream clusters |
| [Harvester profile guide](docs/guide-harvester.md) | Deploy Harvester HCI (2-node, 3-node, HA, full lab) |
| [SUSE Edge profile guide](docs/guide-suse-edge.md) | Deploy the SUSE Edge 3.7 stack (Rancher + Elemental + EIB + edge nodes) |
| [SUSE Telco Cloud profile guide](docs/guide-suse-telco.md) | Management cluster, BMC, image, enrolled hosts. Students apply the manifests |
| [Plain KVM hosts](docs/guide-kvm-hosts.md) | KVM and libvirt only, including a small fleet with no dead-man switch |
| [Bare metal example](docs/examples/bare-metal.md) | Full walkthrough on a physical or cloud host |
| [AWS from a Mac](docs/examples/aws-macos.md) | Step by step from a clean Mac to a lab on AWS |
| [Instruqt example](docs/examples/instruqt.md) | Build an Instruqt track image with a pre-deployed cluster |
| [Install on Linux and macOS](docs/install.md) | KVM host vs control machine installs |
| [Fleet (multi-host)](docs/fleet.md) | A lab per attendee across many hosts, plus the claim portal |
| [Testing and CI](docs/examples/testing.md) | Unit tests, integration tests, GitHub Actions |
| [Agent skill](skills/rodeo/SKILL.md) | Let an AI coding agent drive rodeo for you. For Claude Code: `cp -r skills/rodeo ~/.claude/skills/` |
| [MCP server](docs/mcp.md) | Expose rodeo as MCP tools over stdio: `rodeo mcp`, the tool surface, client registration |

**Reference**

| Document | What's in it |
|----------|-------------|
| [rodeo-plan.yaml reference](docs/reference/plan.md) | Every field in the plan file, annotated |
| [definition.yaml reference](docs/reference/definition.md) | Every field in the topology file, annotated |
| [Create your own rodeo](docs/custom-rodeos.md) | Scaffold and customize a topology |
| [Operations runbook](docs/runbook.md) | Diagnose and recover from common failures |

**Project**

| Document | What's in it |
|----------|-------------|
| [Architecture](docs/architecture.md) | Codebase design for contributors |
| [ROADMAP.md](ROADMAP.md) | Planned features and validation queue |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [SECURITY.md](SECURITY.md) | How to report vulnerabilities |

---

## Development

```bash
pip install -e ".[dev]"
ruff check rodeo tests
pytest tests/ -v
```

---

## Authors

Andres Valero and Raúl Mahiques, Principal Technology Advocates, SUSE
