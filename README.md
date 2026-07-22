<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo/wordmark-dark.png">
  <img alt="rodeo-cli" src="docs/assets/logo/wordmark-light.png" height="60">
</picture>

A CLI for deploying hands-on lab infrastructure. Point it at a Linux host with KVM, pick a profile, and it builds a working lab of nested VMs — Harvester HCI clusters, Rancher Prime, or a full SUSE Edge stack — without you writing a line of Ansible or touching libvirt directly.

[![Release](https://img.shields.io/github/v/release/avaleror/rodeo-cli?sort=semver&label=release)](https://github.com/avaleror/rodeo-cli/releases)
[![CI](https://github.com/avaleror/rodeo-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/avaleror/rodeo-cli/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

**[📖 Docs & user guide](https://avaleror.github.io/rodeo-cli/)**

---

## How it works

A **profile** describes a lab: how many VMs, what they run, how much RAM, what ports to expose on the host. Two YAML files define each profile:

- `rodeo-plan.yaml` — resources, credentials, deployment target (bare metal or Instruqt)
- `definition.yaml` — topology: nodes, network, exposed services, start order

The CLI reads those files and drives a **phase pipeline** through Ansible roles on the target host. Not every phase runs in every profile — the engine `type` decides which ones apply:

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

That is it. The installer clones the repo, sets up a Python environment internally, and links `rodeo` as a system command. No venv to activate, no PATH to set, no sudo prefix — ever.

`rodeo up` self-escalates with sudo, auto-detects an existing lab dir, and ends with the URLs and credentials to log in. It also wraps itself in a **tmux session** automatically, so a dropped SSH or Instruqt connection does not kill a running deploy. Re-attach any time with `tmux attach -t rodeo-<profile>`.

Use `--no-tmux` to skip the tmux wrap in scripts.

To pick a specific profile:

```bash
rodeo up --profile rancher        # Rancher Prime only, ~10 GiB RAM
rodeo up --profile harvester-ha   # 3-node Harvester HA, ~52 GiB RAM
rodeo up --profile harvester      # full lab: 3-node Harvester + Rancher, ~60 GiB RAM
rodeo up --profile suse-edge      # SUSE Edge: Rancher + Elemental + EIB + edge nodes
```

---

## Profiles

| Profile | Engine type | What it deploys | RAM needed |
|---------|-------------|----------------|-----------|
| `rancher` | `rancher` | 1 VM: Rancher Prime on K3s | ~10 GiB |
| `test` | `suse-virt` | 2-node Harvester cluster, no Rancher | ~36 GiB |
| `harvester-ha` | `suse-virt` | 3-node Harvester, no Rancher (3-member etcd HA) | ~52 GiB |
| `harvester-2n` | `suse-virt` | 2-node Harvester + Rancher Prime | ~56 GiB |
| `harvester` | `suse-virt` | 3-node Harvester HCI + Rancher Prime | ~60 GiB |
| `suse-edge` | `suse-edge` | Rancher + Elemental + EIB + edge nodes (SUSE Edge 3.6) | ~40 GiB |

`rodeo doctor` recommends the largest profile that fits available RAM. `rodeo profiles` lists all profiles including any you create yourself. Each profile picks one of three **engine types** (`rancher`, `suse-virt`, `suse-edge`) that decides which pipeline phases run.

You can scaffold and customize your own:

```bash
rodeo new mylab --from harvester
$EDITOR ~/.rodeo/profiles/mylab/definition.yaml
rodeo up --profile mylab
```

Full walkthrough: [Create your own rodeo](docs/custom-rodeos.md).

---

## Commands

| Command | What it does |
|---------|-------------|
| `up` | Front door: host check, install deps, pick lab, generate secrets, deploy, print login info. Wraps in tmux automatically — re-attach with `tmux attach -t rodeo-<profile>` |
| `doctor` | Host readiness check and profile recommendation by available RAM |
| `new` | Scaffold a custom lab from a bundled base: `rodeo new mylab --from harvester` |
| `profiles` | List deployable profiles (bundled + your custom ones in `~/.rodeo/profiles/`) |
| `install-deps` | Install host packages (KVM, libvirt, ansible, kubectl) |
| `init` | Create `rodeo-plan.yaml` and `~/.rodeo/secrets.yaml` |
| `plan` | Preview what deploy would change (no changes made) |
| `deploy` | Run the phase pipeline. Flags: `--from PHASE`, `--force`, `--check`, `--no-tui`, `-P key=value` |
| `status` | VM states, VIP reachability, phase progress |
| `stop` | Graceful stop in reverse definition order (infra-aware) |
| `start` | Start host services and VMs in definition order |
| `clean` | Destroy lab VMs, disks, state. `--all --yes --secrets` for a full host reset |
| `watch` | Split-panel TUI: phase progress + VM serial logs |
| `ssh` | SSH into a lab VM: `rodeo ssh harvester1` |
| `logs` | Tail VM serial log. `--bundle` packages a support tarball |
| `restart` | Restart a single VM |
| `attach` | Serial console (Ctrl+] to detach) |
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
| [Rancher profile guide](docs/guide-rancher.md) | Deploy Rancher Prime on K3s |
| [Harvester profile guide](docs/guide-harvester.md) | Deploy Harvester HCI (2-node, 3-node, HA, full lab) |
| [SUSE Edge profile guide](docs/guide-suse-edge.md) | Deploy the SUSE Edge 3.6 stack (Rancher + Elemental + EIB + edge nodes) |
| [Bare metal example](docs/examples/bare-metal.md) | Full walkthrough on a physical or cloud host |
| [Instruqt example](docs/examples/instruqt.md) | Build an Instruqt track image with a pre-deployed cluster |
| [Testing and CI](docs/examples/testing.md) | Unit tests, integration tests, GitHub Actions |

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

Andres Valero and Raúl Mahiques — Principal Technology Advocates, SUSE
