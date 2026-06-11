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

# 4. Check the host before touching it
rodeo deploy --check

# 5. Deploy
rodeo deploy
```

## Commands

| Command | What it does |
|---|---|
| `install-deps` | Install zypper/apt/dnf packages + ansible-core |
| `init` | Generate `rodeo-plan.yaml` and `~/.rodeo/secrets.yaml` |
| `deploy [--from PHASE] [--force] [--check] [--finalise]` | Run the full pipeline (resume, re-run, preflight-only) |
| `clean` | Destroy VMs, disks, ISOs, and reset phase state |
| `status` | Show VM states, phase progress, and cluster VIP reachability |
| `watch` | Split-panel TUI: deploy progress + serial logs |
| `restart <vm\|all>` | Graceful shutdown + start for one or all VMs |
| `ssh <vm>` | SSH into harvester1/2/3 or rancher |
| `logs <vm>` | Tail the serial console log for a VM |
| `attach <vm>` | Attach to virsh serial console (Ctrl-] to detach) |

## Deployment phases

1. **kvm_host** — Ansible: libvirt, firewalld, storage pool
2. **vms** — Ansible: VM disks, ISOs, cloud-init, domains defined
3. **cluster** — Python: ordered VM start, VIP wait, kubeconfig, 3 nodes Ready
4. **rancher** — Python: K3s + cert-manager + Rancher Prime + Harvester import
5. **finalise** — libvirt-guests enabled, VM autostart set

Resume from any phase, or re-run everything:

```bash
rodeo deploy --from vms
rodeo deploy --force
```

`rodeo deploy --check` runs preflight checks only (root, /dev/kvm, nested virt, RAM, disk, required tools).

### Instruqt guard

With `deployment_target: instruqt` in the plan, the **finalise** phase is skipped: enabling `libvirt-guests` before the Instruqt image snapshot breaks instance boot. After the snapshot, run:

```bash
rodeo deploy --from finalise --finalise
```

On normal hosts set `deployment_target: baremetal` and finalise runs as part of `deploy`.

## Configuration

`rodeo-plan.yaml` in the current directory controls everything. Secrets go in `~/.rodeo/secrets.yaml` (chmod 600, never committed).

Set `RODEO_ANSIBLE_PATH` or `ansible.path` in the plan to point at the directory containing `ansible/playbook.yml`. By default the Ansible roles bundled with the package are used.

### Passwords

`rodeo init` writes the lab password to `~/.rodeo/secrets.yaml` (chmod 600). Pick where it comes from:

```bash
rodeo init                      # random 16-char password
rodeo init --ask                # hidden interactive prompt
RODEO_PASSWORD=... rodeo init   # from the environment (CI / Instruqt setup)
```

The plan references secrets with `??` placeholders, which also support external sources:

| Placeholder | Source |
|---|---|
| `??harvester_os_password` | `~/.rodeo/secrets.yaml` |
| `??env:RODEO_PASSWORD` | environment variable |
| `??file:/run/secrets/pw` | file contents |
| `??cmd:pass show rodeo` | command stdout (`pass`, `op`, `vault` ...) |

If a source resolves to nothing, `rodeo deploy` fails fast instead of deploying with an empty password. Passwords never appear on the ansible command line (they travel in a chmod-600 vars file) and the tasks that render them run with `no_log`.

Note: `??cmd:` runs a shell command from the plan file, so treat `rodeo-plan.yaml` with the same care as a Makefile.

## Requirements

- Linux host with KVM (`/dev/kvm`)
- ~64 GB RAM, ~32 vCPU (fits 3x16 GiB Harvester + 8 GiB Rancher)
- ~900 GB free disk in the libvirt image pool
- Python 3.10+
