# rodeo-cli

CLI tool to deploy and manage the SUSE Virtualization Rodeo — a nested KVM lab running a 3-node Harvester HCI cluster plus Rancher Prime, used for the Instruqt-based SUSE Virtualization Rodeo training lab.

## Install

```bash
pip install -e .
```

## Quick start

```bash
# 1. Install system packages (SLES 16 / Leap 16 / Ubuntu / Fedora)
sudo rodeo install-deps

# 2. Generate a deployment plan
rodeo init

# 3. Edit the plan and set passwords
$EDITOR rodeo-plan.yaml
$EDITOR ~/.rodeo/secrets.yaml

# 4. Deploy
rodeo deploy
```

## Commands

| Command | What it does |
|---|---|
| `install-deps` | Install zypper/apt/dnf packages + ansible-core |
| `init` | Generate `rodeo-plan.yaml` and `~/.rodeo/secrets.yaml` |
| `deploy [--from PHASE]` | Run the full pipeline (or resume from a phase) |
| `clean` | Destroy VMs, disks, ISOs, and reset phase state |
| `status` | Show VM states and cluster VIP reachability |
| `watch` | Split-panel TUI: deploy progress + serial logs (v0.2) |
| `restart <vm\|all>` | Graceful shutdown + start for one or all VMs |
| `ssh <vm>` | SSH into harvester1/2/3 or rancher |
| `logs <vm>` | Tail the serial console log for a VM |
| `attach <vm>` | Attach to virsh serial console (Ctrl-] to detach) |

## Deployment phases

1. **preflight** — disk, CPU, memory, KVM module checks
2. **kvm_host** — Ansible: libvirt, firewalld, storage pool
3. **vms** — Ansible: VMs created + Harvester cluster bootstrap
4. **rancher** — Ansible: K3s + cert-manager + Rancher Prime
5. **finalise** — libvirt-guests enabled, VM autostart set

Resume from any phase:

```bash
rodeo deploy --from vms
```

## Configuration

`rodeo-plan.yaml` in the current directory controls everything. Secrets go in `~/.rodeo/secrets.yaml` (chmod 600, never committed).

Set `RODEO_ANSIBLE_PATH` or `ansible.path` in the plan to point at the directory containing `ansible/site.yml`.

## Requirements

- Linux host with KVM (`/dev/kvm`)
- ~64 GB RAM, ~32 vCPU (fits 3x16 GiB Harvester + 8 GiB Rancher)
- ~900 GB free disk in the libvirt image pool
- Python 3.10+
