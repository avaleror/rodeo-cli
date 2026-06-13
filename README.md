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

New to this? Two commands.

```bash
rodeo doctor   # is my host ready, and which lab fits my RAM?
rodeo up       # set up + deploy a lab, then show how to log in
```

`rodeo up` checks the host, offers to install missing deps, picks a lab sized for your
machine, generates secrets for you, escalates with sudo on its own, and ends with the
URLs and password to log in. No `source`, no `sudo -E`, no `--config-dir`.

**Installing the `rodeo` command first** (on a clean Linux host with Python 3.10+):

```bash
git clone https://github.com/avaleror/rodeo-cli.git
cd rodeo-cli
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -e .
rodeo up
```

Advanced / scripted setups (custom topologies, Instruqt images, pinned versions) use
`generate`, `init`, `bootstrap`, and the explicit `plan` / `deploy` commands. See the
[User guide](docs/user-guide.md).

---

## Commands

| Command | Description |
|---------|-------------|
| `up` | **Start here.** One command: doctor → pick a lab that fits → secrets → deploy → login info. Self-escalates with sudo. |
| `doctor` | Host readiness (RAM, CPU, disk, KVM, nested virt, tools) and which lab profile fits |
| `bootstrap` | (advanced) One-command host link + ready lab dir setup for clean SLES |
| `generate` | (advanced) Custom definition + full config-dir skeleton from templates. `rodeo generate --dir mylab` |
| `install-deps` | Host packages (KVM, ansible, kubectl, …) + optional `--link` for /usr/local/bin/rodeo |
| `init` | Create `rodeo-plan.yaml` + `~/.rodeo/secrets.yaml` (supports `--example` for pre-seeded configs) |
| `plan` | Preview diff vs host (no changes) |
| `deploy` | Run the full pipeline |
| `stop` | Graceful infra-aware stop of the lab (VMs + host services per definition; --all for everything). VMs stay defined for restart. |
| `start` | Start the lab after stop (host services + VMs per definition). |
| `status` | VM states, VIP, phase progress |
| `clean` | Destroy lab VMs, disks, reset state. `--all --yes --secrets --force-network --hard` for full host reset (all rodeo VMs/networks/states/passwords; leaves packages + rodeo binary for repurposing or fresh start). |
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
- **Secrets:** `~/.rodeo/secrets.yaml` (chmod 600, never commit; `rodeo generate` warns instead of silent clobber if exists)
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