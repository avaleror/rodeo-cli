# rodeo-cli

Deploy and manage infrastructure for **rodeos** — live, hands-on workshops where attendees work on real systems.

The default lab (**suse-virt**) provisions a 3-node [Harvester](https://harvesterhci.io/) HCI cluster and [Rancher Prime](https://www.rancher.com/) on nested KVM, on a single Linux host. Harvester nodes install via **iPXE network boot** (UEFI PXE, not ISO-first). Use it on **Instruqt**, **cloud VMs**, **local VMs**, or **bare metal**.

**Version:** 0.4.0 · **Python:** 3.10+ · **License:** Apache-2.0

---

## Documentation

| Guide | For |
|-------|-----|
| **[User guide](docs/user-guide.md)** | Workshop operators — install, deploy, Instruqt workflow, day-2 ops |
| **[Architecture](docs/architecture.md)** | Contributors — design, pipeline, Ansible/Python split, constraints |
| [ROADMAP.md](ROADMAP.md) | Planned features (Terraform-for-labs direction) |
| [CONTEXT.md](CONTEXT.md) | Full project context for development |

---

## Quick start

```bash
pip install -e .
sudo rodeo install-deps
rodeo init
rodeo deploy --check
rodeo plan
rodeo deploy
```

---

## Commands

| Command | Description |
|---------|-------------|
| `install-deps` | Host packages (KVM, ansible, kubectl, …) |
| `init` | Create `rodeo-plan.yaml` + `~/.rodeo/secrets.yaml` |
| `plan` | Preview diff vs host (no changes) |
| `deploy` | Run the full pipeline |
| `status` | VM states, VIP, phase progress |
| `clean` | Destroy lab VMs, disks, reset state |
| `watch` | TUI: phases + serial logs |
| `ssh` / `logs` / `restart` / `attach` | VM access and ops |
| `logs --bundle` | Support tarball for troubleshooting |

`deploy` options: `--from PHASE`, `--force`, `--check`, `--finalise`, `--no-tui`, `-P key=value`, `--paramfile FILE`.

---

## What gets deployed

| VM | Role | Default IP |
|----|------|------------|
| harvester1–3 | Harvester HCI nodes | .11 – .13 |
| rancher | Rancher Prime on K3s | .9 |
| (VIP) | Harvester API/UI | .10 |

**Host sizing (default):** ~64 GiB RAM, ~32 vCPU, ~900 GiB disk.

---

## Instruqt vs bare metal

```yaml
# rodeo-plan.yaml
deployment_target: instruqt   # or baremetal
```

- **instruqt** — skips `finalise` until after image snapshot (prevents broken instance boot)
- **baremetal** — full deploy including VM autostart on host reboot

After an Instruqt snapshot:

```bash
rodeo deploy --from finalise --finalise
```

Details: [User guide — Deployment targets](docs/user-guide.md#deployment-targets-in-detail).

---

## Configuration

- **Plan:** `rodeo-plan.yaml` (in your working directory)
- **Secrets:** `~/.rodeo/secrets.yaml` (chmod 600, never commit)
- **Overrides:** `-P resources.harvester.memory_mib=20480` or `--paramfile lab.yaml`
- **State:** `~/.rodeo/state/<plan-name>.yaml`

---

## Development

```bash
pip install -e ".[dev]"
ruff check rodeo tests
pytest tests/ -v
```

CI runs on Python 3.10 and 3.12.

---

## Author

Andres Valero — Principal Technology Advocate, SUSE