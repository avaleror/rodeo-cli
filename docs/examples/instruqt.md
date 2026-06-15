# Example: Instruqt track image build

This example shows how to build an Instruqt track with a pre-deployed Harvester lab. Attendees start with a running cluster — no waiting for iPXE install during the workshop.

## How it works

Instruqt gives you a **builder instance** where you deploy the lab. You then take a snapshot. When an attendee starts a challenge, they get a copy of that snapshot with the cluster already running.

The key difference from bare metal: the `finalise` phase (which enables VM autostart) must run **after** the snapshot, not before. If autostart is enabled on the snapshot, the next boot can hang before the Instruqt agent connects.

## Host sizing on Instruqt

Use a `n2-standard-32` (or equivalent) instance with nested virtualization enabled:

| Resource | Value |
|----------|-------|
| RAM | 128 GiB |
| CPU | 32 vCPU |
| Disk | 1–2 TB |
| Nested virt | Required |

Request the `geekohive` machine type in the Instruqt sandbox config if you are using SUSE's Instruqt organization.

## Step 1: install rodeo-cli on the builder

```bash
git clone https://github.com/avaleror/rodeo-cli.git
cd rodeo-cli
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -e .
rodeo install-deps --link
```

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
2. If VMs are defined but not running, `finalise` did not enable autostart before the snapshot.
3. Start them manually to recover: `rodeo start --all --yes`
4. Re-snapshot with `finalise` running post-snapshot next time.

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
cd rodeo-cli && source .venv/bin/activate 2>/dev/null || (python3 -m venv --system-site-packages .venv && source .venv/bin/activate && pip install -e .)
rodeo deploy --from finalise --finalise
```
