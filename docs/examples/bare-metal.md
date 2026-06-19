# Example: bare metal deployment

This example walks through deploying the full `harvester` profile (3-node Harvester HCI + Rancher Prime) on a bare metal Linux host. The same steps apply to large cloud VMs with nested virtualization enabled.

## Host requirements

| Resource | Minimum |
|----------|---------|
| OS | SLES 16, Leap 16, Ubuntu 22.04, or Fedora 39+ |
| RAM | 64 GiB (3 × 16 GiB Harvester + 8 GiB Rancher + host overhead) |
| CPU | 28 vCPU to spare |
| Disk | 900 GiB free in `/var/lib/libvirt/images` (or a dedicated second disk) |
| KVM | `/dev/kvm` present; no nested virt needed on bare metal |

## Step 1: install rodeo-cli

On a clean SLES 16 / Leap 16 host:

```bash
curl -fsSL https://raw.githubusercontent.com/avaleror/rodeo-cli/main/install.sh | bash
```

This clones the repo, sets up a Python environment internally, and links `rodeo` as a system command. No venv to activate, no PATH to set.

To install KVM packages, libvirt, ansible-core, and kubectl separately:

```bash
rodeo install-deps
```

`rodeo up` (Step 3) runs this automatically if deps are missing.

## Step 2: check the host

```bash
rodeo doctor
```

Expected output for a ready host:

```
Host facts
  ✓  root
  ✓  /dev/kvm
  ✓  nested virt        (not required on bare metal, shown as warning if absent)
  ✓  RAM     120 GiB total, 100 GiB available
  ✓  disk    900 GiB free in /var/lib/libvirt/images
  ✓  ansible-playbook
  ✓  ansible-galaxy
  ✓  kubectl
  ✓  python: libvirt
  ✓  python: lxml

Recommended profile: harvester (60 GiB needed, 100 GiB available)
```

## Step 3: deploy

```bash
rodeo up --profile harvester
```

`rodeo up` will:
1. Start a tmux session named `rodeo-harvester` so the deploy survives disconnects
2. Confirm the host checks pass
3. Generate `~/.rodeo/secrets.yaml` with random credentials
4. Seed the lab directory into `~/rodeo-labs/harvester/`
5. Self-escalate with sudo and start the deploy pipeline
6. Print login info when done

**If your SSH session drops**, re-attach to the running deploy:

```bash
tmux attach -t rodeo-harvester
```

Detach without stopping the deploy: `Ctrl+b  d`.

Open a second tmux window (`Ctrl+b  c`) to watch progress while the deploy runs:

```bash
rodeo watch         # split-panel TUI: phases + serial logs
rodeo logs harvester1   # or tail a single node
```

Total time: 90–150 minutes (most of it is Harvester iPXE install on nested KVM; bare metal is significantly faster).

## Step 4: log in

After `rodeo up` finishes:

- **Harvester UI:** `https://<host-ip>:8443`
- **Rancher UI:** `https://<host-ip>:30002`
- **Username:** `admin`
- **Password:** shown in the success output, or check `~/.rodeo/secrets.yaml`

## Using a dedicated second disk

If your host has a separate data disk (check with `lsblk`), configure it in `rodeo-plan.yaml`:

```yaml
storage:
  device: /dev/nvme1n1    # your data disk — verify with lsblk first
  image_dir: /var/lib/libvirt/images
```

The `kvm_host` Ansible role will prepare and mount it before provisioning VMs.

## Redeploying after a clean

```bash
rodeo clean --all --yes --secrets   # destroy everything, wipe credentials
rodeo up --profile harvester        # fresh deploy with new credentials
```

## Multi-disk host with separate OS and data disks

Typical bare metal server: OS on `/dev/nvme0n1`, data on `/dev/nvme1n1`.

```yaml
# rodeo-plan.yaml
storage:
  device: /dev/nvme1n1
  mount_point: /var/lib/libvirt/images
  image_dir: /var/lib/libvirt/images
  fs_type: xfs
```

The `kvm_host` role partitions the device, creates an XFS filesystem, and mounts it at `image_dir` if it is not already mounted.

## Day-2 operations

| Task | Command |
|------|---------|
| Health check | `rodeo status` |
| SSH into a node | `rodeo ssh harvester1` |
| Tail serial log | `rodeo logs harvester2` |
| Graceful stop | `rodeo stop --all --yes` |
| Start after stop | `rodeo start --all --yes` |
| Destroy the lab | `rodeo clean --yes` |
| Full host reset | `rodeo clean --all --yes --secrets` |
