# Example: Instruqt track image build

This example shows how to build an Instruqt track with a pre-deployed Harvester lab. Attendees start with a running cluster — no waiting for iPXE install during the workshop.

## How it works

Instruqt gives you a **builder instance** where you deploy the lab. You then **Save** a hostimage (snapshot). When an attendee starts a challenge, they get a copy of that image.

The key difference from bare metal: **do not bake `finalise` into the hostimage.** That phase enables VM autostart + `libvirt-guests`. If those are on when the image boots, nested KVM / virbr0 can race cloud-init and leave the Instruqt console on **Please Wait**. With `deployment_target: instruqt`, rodeo skips `finalise` automatically. Bring the lab up on every instance with **`rodeo start-if-needed`** in the track setup script instead.

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
deployment_target: instruqt   # critical — skip finalise (never bake it into the hostimage)
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

The `finalise` phase is skipped automatically when `deployment_target: instruqt`. That is what you want for a hostimage.

Verify the cluster is healthy before Save:

```bash
rodeo status
```

Expected: all 3 Harvester nodes Ready, VIP reachable, Rancher UI accessible.

## Step 4: hostimage checklist (before Save)

The success screen after deploy prints the same checklist. Confirm before you click **Save**:

| Check | Why |
|-------|-----|
| Do **not** run `rodeo deploy --finalise` | Bakes `libvirt-guests` + VM autostart → can hang boot / **Please Wait** |
| `firewall-cmd --list-ports` includes **15778/tcp** and **15779/tcp** | Instruqt terminal/editor agent ports |
| `systemctl is-enabled libvirt-guests` is **disabled** | Must stay off in the image |
| Track setup will run `rodeo start-if-needed` | Starts VMs + re-applies DNAT/nft on every boot |

Optional: `rodeo stop --all --yes` before Save for cleaner nested guest disks (etcd/FS). Not required for agent connectivity.

**Save captures disk state at click time.** Running `finalise` *after* Save only changes the live builder; it does **not** update an already-saved hostimage. Re-Saving after finalise is how images get poisoned — don't do that for workshop hostimages.

## Step 5: take the Instruqt snapshot

Use the Instruqt UI (**Save** hostimage) or API. At this point the cluster may still be running on the builder, but VMs must **not** be set to autostart in the image — which is correct.

## Step 6: track setup + verify on an attendee instance

**Wire `rodeo start-if-needed` into the track's attendee boot/setup script** (whatever runs when Instruqt starts a new instance from the hostimage). This is not optional for a healthy lab:

- Nested VMs do not autostart (by design — no finalise in image).
- The libvirt qemu hook that reapplies DNAT + `guest_input` accept only fires reliably on a genuine VM start — not on a cold hostimage boot with VMs left "as was."
- `start-if-needed` is idempotent: starts host services + VMs if needed, then enforces nft rules.

```bash
rodeo start-if-needed
```

Check from inside the instance:

```bash
rodeo status
```

If VMs are shut off or UIs are unreachable after boot, the setup script likely omitted `start-if-needed` — add it and re-test; do **not** "fix" by baking finalise into a new Save.

## The firewalld timing constraint

The `kvm_host` Ansible role **disables, stops, and masks firewalld** during host prep (early in the build, before any VM exists). This is intentional: on SLES 16, firewalld integrates with NetworkManager via D-Bus, and cloud-init doesn't assign a zone to the external NIC — starting firewalld before that's sorted out can drop the Instruqt management connection.

firewalld itself is unmasked and started later, in the `cluster` phase — well before the snapshot, not by `finalise`. This is safe because that same step also runs, on Instruqt:

```
firewall-cmd --zone=public --change-interface=<ext-iface> --permanent
```

The `--permanent` flag writes the interface→zone mapping into firewalld's own persisted config. On a fresh attendee boot, firewalld reads that mapping directly instead of negotiating a live zone assignment with NetworkManager over D-Bus — which is what removes the race in the first place. So firewalld being enabled and running in the snapshot is expected and fine.

What actually matters before you snapshot: confirm this pinning step ran and picked the right interface. Check the deploy log for `Instruqt: pinning <iface> to public zone...`, and confirm with `firewall-cmd --get-active-zones` that your real management NIC shows under `public`. Re-check the same command on the first attendee boot from the snapshot — that's the actual test that the pinning held.

`finalise` must never be baked into the hostimage: it enables `libvirt-guests` + VM autostart, which races nested boot against the Instruqt agent and can leave the console on **Please Wait**.

## Credentials in the snapshot

Credentials are baked into the snapshot via `~/.rodeo/secrets.yaml`. All attendee instances share the same credentials. This is expected for workshop use — do not use production passwords.

Default credential location on the Instruqt instance:

```bash
cat ~/.rodeo/secrets.yaml
```

## Troubleshooting Instruqt-specific issues

### Console stuck on "Please Wait" after Save / start hostimage

The Instruqt terminal and editor tabs talk to the **agent on the host** (TCP
**15778** and **15779**), not to SSH alone. After `cluster` (or `boot`) unmasks
and starts firewalld, those ports must be open on the public zone — otherwise a
cold boot of the saved hostimage leaves the UI on **Please Wait** even when the
OS is up.

rodeo opens them when `deployment_target: instruqt` (Ansible `kvm_host` firewall
tasks + re-assert in `_start_firewalld`). On an older image that was built before
that fix, recover once then re-Save:

```bash
sudo firewall-cmd --permanent --zone=public --add-port=15778/tcp
sudo firewall-cmd --permanent --zone=public --add-port=15779/tcp
sudo firewall-cmd --reload
# or re-run: rodeo deploy --from cluster   # re-asserts ports for instruqt target
```

Also confirm you did **not** bake `finalise` into the image before Save
(`libvirt-guests` enabled + VM autostart can stall `network-online.target` and
block the agent). Prefer `deployment_target: instruqt` and
`rodeo start-if-needed` on attendee boot instead of finalise-in-image.

### Cluster not reachable after attendee instance boots

1. Check if VMs started: `virsh list --all`
2. If VMs are defined but not running, the track setup script probably did not run `rodeo start-if-needed` — run it now and add it to setup permanently. Do **not** bake `finalise` into a re-Save just to get autostart.
3. If VMs **are** running but the Harvester/Rancher UI is still unreachable, this is almost always the nftables rules, not the VMs: the qemu hook that reapplies the DNAT + `guest_input`-accept rules does not reliably fire on hostimage boot. Run:
   ```bash
   rodeo start-if-needed
   ```
   `rodeo start --all --yes` is **not** a substitute here — it starts VMs but does not touch nftables, so it won't fix this specific failure mode.
4. Wire `rodeo start-if-needed` into the attendee boot/setup script so this self-heals on every instance start.

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

### Should I run `rodeo deploy --finalise` on Instruqt?

**For hostimages: no.** Keep finalise out of the image and use `rodeo start-if-needed` on boot.

`finalise` remains available for bare metal (and rare advanced cases with `--finalise`). If you enable it on a builder and then Save, you risk the next hostimage start sticking on **Please Wait**.
