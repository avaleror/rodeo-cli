# Example: Instruqt track image build

This example shows how to build an Instruqt track with a pre-deployed Harvester lab. Attendees start with a running cluster — no waiting for iPXE install during the workshop.

## How it works

Instruqt gives you a **builder instance** where you deploy the lab. You then take a snapshot. When an attendee starts a challenge, they get a copy of that snapshot with the cluster already running.

The key difference from bare metal: the `finalise` phase (which enables VM autostart) must run **after** the snapshot, not before. If autostart is enabled on the snapshot, the next boot can hang before the Instruqt agent connects.

## Host sizing on Instruqt

Use a generous builder (`n2-standard-32` or ~40 vCPU) with nested virtualization enabled:

| Resource | Value |
|----------|-------|
| RAM | 128 GiB |
| CPU | 32–40 vCPU |
| Disk | 1–2 TB |
| Nested virt | Required |

Request the `geekohive` machine type in the Instruqt sandbox config if you are using SUSE's Instruqt organization.

**Guest vCPU budget:** keep Σ guest vCPU ≤ ~70% of host logical CPUs. When you seed with
`deployment_target: instruqt` (`rodeo up` on an Instruqt host, or
`rodeo up --deployment-target instruqt`), rodeo applies host-aware presets — typically
**6–8 vCPU / 20 GiB** per Harvester node and **4 vCPU / 8 GiB** for Rancher on a
32–40 vCPU builder. Override anytime with `-P resources.harvester.vcpu=…`.

Guest disk cache defaults to `writeback`/`threads` on Instruqt (see `libvirt.disk_cache`
in the plan reference) — better nested cloud I/O than bare-metal `none`/`native`.

## Step 1: install rodeo-cli on the builder

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

This clones the repo, sets up a Python environment internally, links `rodeo` as a system command, and installs host dependencies (KVM, libvirt, ansible, kubectl).

## Step 2: set deployment target

Create your lab directory and set the target to `instruqt`:

```bash
rodeo new mylab --from harvester
```

Edit `~/.rodeo/profiles/mylab/rodeo-plan.yaml`:

```yaml
deployment_target: instruqt   # critical — skip finalise until after snapshot
```

Or use the bundled `harvester` profile directly and override at deploy time:

```bash
rodeo deploy -P deployment_target=instruqt
```

## Step 3: deploy the lab

```bash
rodeo up --profile harvester --yes
```

`rodeo up` immediately wraps itself in a tmux session named `rodeo-harvester`. This is critical on Instruqt: the browser tab can time out or close during a 90–150 minute deploy, and without tmux the process dies and leaves the host in a broken state.

**If your Instruqt tab closes or times out**, re-open the terminal tab and re-attach:

```bash
tmux attach -t rodeo-harvester
```

Detach without stopping the deploy: `Ctrl+b  d`.

The `finalise` phase is skipped automatically when `deployment_target: instruqt`. The pipeline stops after `rancher`.

Verify the cluster is healthy before snapshotting:

```bash
rodeo status
```

Expected: all 3 Harvester nodes Ready, VIP reachable, Rancher UI accessible.

## Step 4: take the Instruqt snapshot

Use the Instruqt UI or API to snapshot the builder instance. At this point the cluster is running but VMs do not autostart on reboot — which is correct for the snapshot.

## Step 5: enable autostart (post-snapshot)

After the snapshot is saved, run `finalise` on the builder:

```bash
rodeo deploy --from finalise --finalise
```

This enables `libvirt-guests` and sets all lab VMs to autostart. The builder instance is now done.

## Step 6: verify on an attendee instance

Start a fresh attendee instance from the snapshot. The VMs should come up automatically and the cluster should be reachable within a few minutes of boot.

**Wire `rodeo start-if-needed` into the track's attendee boot/setup script** (whatever
runs when Instruqt starts a new instance from the snapshot). This is not optional: the
libvirt qemu hook that reapplies the DNAT + `guest_input`-accept nftables rules only
fires on a genuine VM start event — it does not reliably fire when `libvirt-guests`
brings VMs back on a resumed/snapshotted boot. Without an explicit
`rodeo start-if-needed` call, the cluster comes up internally but stays unreachable
from outside (Harvester UI / Rancher UI both silently broken) even though `finalise`
ran correctly on the builder. `start-if-needed` is idempotent — safe to call even if
the VMs are already up — so it's the right thing to run unconditionally on every
attendee boot, not just as a recovery step:

```bash
rodeo start-if-needed
```

Check from inside the instance:

```bash
rodeo status
```

If VMs do not start automatically, the `finalise` step may have been skipped or run before the snapshot. Re-snapshot from a builder where `finalise` ran after the snapshot.

## The firewalld timing constraint

The `kvm_host` Ansible role **disables and stops firewalld** during the build phase. This is intentional: on SLES 16, firewalld integrates with NetworkManager via D-Bus. If it starts and integrates with the `eth0` zone during an Instruqt build save/restart, it can drop the Instruqt management connection.

The `finalise` phase re-enables firewalld. Do not enable it manually before snapshotting.

## Credentials in the snapshot

Credentials are baked into the snapshot via `~/.rodeo/secrets.yaml`. All attendee instances share the same credentials. This is expected for workshop use — do not use production passwords.

Default credential location on the Instruqt instance:

```bash
cat ~/.rodeo/secrets.yaml
```

## Troubleshooting Instruqt-specific issues

### Cluster not reachable after attendee instance boots

1. Check if VMs started: `virsh list --all`
2. If VMs are defined but not running, `finalise` did not enable autostart before the snapshot — re-snapshot from a builder where `finalise` ran post-snapshot.
3. If VMs **are** running but the Harvester/Rancher UI is still unreachable, this is almost always the nftables rules, not the VMs: the qemu hook that reapplies the DNAT + `guest_input`-accept rules doesn't reliably fire when `libvirt-guests` resumes VMs on boot (see Step 6). Run:
   ```bash
   rodeo start-if-needed
   ```
   `rodeo start --all --yes` is **not** a substitute here — it starts VMs but does not touch nftables, so it won't fix this specific failure mode.
4. Going forward, wire `rodeo start-if-needed` into the attendee boot/setup script so this self-heals on every instance start instead of needing manual recovery.

### iPXE install hangs during build

Instruqt nested KVM performance varies. If the iPXE install times out:

```bash
rodeo logs harvester1   # check where it is in the install
rodeo deploy --from cluster --force   # resume the cluster phase
```

### firewalld drops the Instruqt management connection

If you accidentally enabled firewalld before the snapshot and the builder instance loses connectivity:

- Access the instance via the Instruqt emergency console
- Run: `systemctl stop firewalld`
- Restore connectivity, then re-snapshot with firewalld stopped

### `rodeo deploy --from finalise --finalise` fails after snapshot

If the builder instance was recycled and the venv is gone, re-install rodeo-cli first:

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
rodeo deploy --from finalise --finalise
```
