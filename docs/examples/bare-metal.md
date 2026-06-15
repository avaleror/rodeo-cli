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
git clone https://github.com/avaleror/rodeo-cli.git
cd rodeo-cli
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -e .
```

## Step 2: install host dependencies

This installs KVM packages, libvirt daemons, ansible-core, ansible collections, and kubectl:

```bash
rodeo install-deps --link
```

`--link` creates `/usr/local/bin/rodeo` so you can run `rodeo` from anywhere without activating the venv.

## Step 3: check the host

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

## Step 4: deploy

```bash
rodeo up --profile harvester
```

`rodeo up` will:
1. Confirm the host checks pass
2. Generate `~/.rodeo/secrets.yaml` with random credentials
3. Seed the lab directory into `~/rodeo-labs/harvester/`
4. Self-escalate with sudo and start the deploy pipeline
5. Print login info when done

Watch progress in a separate terminal:

```bash
rodeo watch
```

Or tail a specific node's serial log:

```bash
rodeo logs harvester1
```

Total time: 90–150 minutes (most of it is Harvester iPXE install on nested KVM; bare metal is significantly faster).

## Step 5: log in

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
