# Example: testing and CI

This example covers running rodeo-cli in automated and testing contexts — from fast unit tests on any machine to full integration tests on a KVM host.

## Two tiers of testing

| Tier | What it covers | Where it runs | Time |
|------|----------------|---------------|------|
| Unit / lint | Config parsing, CLI surface, preflight logic, labseed, phase event model | Any machine (no KVM) | ~1 min |
| Integration | Full deploy pipeline: kvm_host → vms → pxe_server → cluster → rancher | Self-hosted KVM host | 1–3 h per profile |

You need both. Unit tests catch regressions in Python code. Integration tests catch the real failures: disk sizing, network, iPXE chain, etcd bootstrap.

## Unit tests

Run on any OS — no KVM, no libvirt, no sudo needed:

```bash
pip install -e ".[dev]"
ruff check rodeo tests
pytest tests/ -v
# Ansible roles (optional locally; required in CI):
pip install "ansible-lint>=25" "ansible-core>=2.16,<2.19"
ansible-galaxy collection install -r rodeo/data/ansible/requirements.yml
ansible-lint rodeo/data/ansible
```

What the unit tests cover:

| Test file | Area |
|-----------|------|
| `test_config.py` | Plan loading, Jinja2 rendering, `-P` overrides, `??` secret resolvers, validation |
| `test_runner.py` | Pipeline events, Instruqt guard, vars file generation |
| `test_ansible_consistency.py` | Profile ↔ Ansible defaults ↔ plan template alignment |
| `test_ansible_vars_contract.py` | Vars file keys match what Ansible roles consume |
| `test_pxe_integration.py` | Playbook order, pxe_server role, boot order, JOIN VIP guard |
| `test_cluster.py` | ClusterPhase poll loops, timeouts, node parsing |
| `test_rancher.py` | RancherPhase API call sequence, timeout handling |
| `test_plan_cmd.py` | `rodeo plan` diff output |
| `test_labseed.py` | Profile scaffolding, `rodeo new`, secrets generation |
| `test_preflight.py` | `detect_host`, `recommend_profile`, RAM/disk checks |

### Test the CLI surface without KVM

`rodeo up --no-deploy` seeds a lab and runs preflight but stops before the deploy pipeline starts. Useful for validating the on-ramp flow in CI without a KVM host:

```bash
rodeo up --no-deploy --yes --profile test --dir /tmp/test-lab
ls /tmp/test-lab   # rodeo-plan.yaml + definition.yaml should be present
cat ~/.rodeo/secrets.yaml   # auto-generated if it did not exist
```

### Test config loading and overrides

```bash
# Dry-run: load and validate a plan, print what would deploy
cd /tmp/test-lab
rodeo plan

# Override a value and see it reflected
rodeo plan -P resources.harvester.memory_mib=20480
```

## Integration tests — manual

On a host that meets the [bare metal requirements](bare-metal.md#host-requirements), run one profile at a time:

```bash
# Clean any previous state first
rodeo clean --all --yes --secrets --force-network

# Deploy and verify (--no-tmux skips the session wrap for scripted runs)
rodeo up --profile test --yes --no-tmux
rodeo status

# Clean up for the next run
rodeo clean --all --yes --secrets --force-network
```

### Profile test order (smallest to largest)

Run in this order to fail fast on smaller profiles before investing time in bigger ones:

1. `rancher` (~10 min) — fastest; validates the boot + Rancher path with no PXE
2. `test` (~60 min) — 2-node Harvester; validates the full iPXE chain
3. `harvester-ha` (~90 min) — 3-node Harvester; validates 3-member etcd HA
4. `harvester` (~120 min) — full lab; validates Rancher import on top of Harvester

## Integration tests — GitHub Actions (self-hosted runner)

The integration workflow is at `.github/workflows/integration.yml`. It runs on a self-hosted runner with the labels `self-hosted`, `kvm`, `sles16`.

### Trigger manually

From the GitHub UI: Actions → Integration tests → Run workflow → select a profile or leave blank for all.

From the CLI:

```bash
# All profiles (nightly-style)
gh workflow run integration.yml

# One profile only
gh workflow run integration.yml -f profile=test
```

### What each job does

1. **Pre-run cleanup** — wipes whatever the previous run left (`rodeo clean --all --yes --secrets --force-network`), ignores failures so a broken previous run does not block the next one
2. **Deploy** — `rodeo up --profile <name> --yes`
3. **Verify** — `rodeo status` (asserts VMs are running and VIP is reachable)
4. **Post-run cleanup** — always runs, even if deploy failed

`max-parallel: 1` ensures only one profile runs at a time on the shared KVM host. `fail-fast: false` means all four profiles run even if one fails, giving you the full picture.

### Setting up a self-hosted runner

See [CONTRIBUTING.md](https://github.com/avaleror/rodeo-cli/blob/main/CONTRIBUTING.md) or the [GitHub Actions runner docs](https://docs.github.com/en/actions/hosting-your-own-runners/adding-self-hosted-runners) for setup steps. The runner must have:

- `rodeo install-deps` run once (KVM, libvirt, ansible, kubectl)
- `rodeo` binary on PATH (via `install-deps --link`)
- Passwordless sudo for the runner user

### Nightly schedule

The workflow also runs nightly at 02:00 UTC via a cron trigger. Failed runs create a failed check on the `main` branch.

## Testing a specific Ansible role change

When you change an Ansible role (e.g. `roles/kvm_host/tasks/firewall.yml`), the unit tests do not catch regressions — they do not run Ansible. You must run a live integration test before merging.

Minimum regression for a role change:

```bash
rodeo deploy --from kvm_host --force   # re-run the changed role
rodeo status                           # verify the cluster still works
```

For the PXE chain (`pxe_server` role), the minimum is a full `test` profile deploy from scratch — the boot chain is only validated end-to-end when Harvester installs successfully.

## Testing definition.yaml changes

After editing a `definition.yaml`, validate it before deploying:

```bash
rodeo plan --profile mylab   # must succeed with no errors
```

Then test with the smallest matching profile:

```bash
rodeo clean --all --yes --secrets
rodeo up --profile mylab --yes
rodeo status
```

If the definition changes node count or IPs, the entire deploy must run from scratch — do not use `--from cluster` because the VM disk and DHCP lease changes come from the `vms` phase.

## Testing secret resolution

```bash
# Verify all ?? placeholders resolve
rodeo deploy --check

# Test a specific resolver
RODEO_PASSWORD=testpassword rodeo plan -P credentials.harvester_os_password=??env:RODEO_PASSWORD
```

`deploy --check` runs the preflight suite (including secret validation) without starting any phase.
