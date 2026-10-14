# Rancher Prime on K3s: profile guide

This guide covers the two Rancher profiles. Both run **Rancher Prime on K3s** on its own VM, with no Harvester, plus downstream clusters that Rancher itself provisions on small lab VMs:

| Profile | Downstream clusters | VMs | RAM |
|---------|--------------------|-----|-----|
| `rancher-test` | `k3s-single` (1-node K3s), `rke2-single` (1-node RKE2) | 3 | ~18 GiB |
| `rancher` | `k3s-single`, `rke2-single`, `rke2-ha` (3-node RKE2, etcd HA) | 6 | ~30 GiB |

The downstream nodes are sized at the minimum each distribution needs (K3s 2 vCPU / 2 GiB, RKE2 2 vCPU / 4 GiB), so these labs are for testing, not for workloads.

Use them when the workshop or demo focuses on **Rancher multi-cluster management** rather than Harvester HCI.

---

## What you get

| Component | Default IP | Port |
|-----------|-----------|------|
| Rancher Prime (Rancher UI) | 192.168.122.9 | 30002 (NodePort) |
| Rancher UI via host DNAT | host IP | 30002 |
| `k3s-single` node `k3s` | 192.168.122.41 | |
| `rke2-single` node `rke2` | 192.168.122.42 | |
| `rke2-ha` nodes `rke2-ha1..3` (`rancher` only) | 192.168.122.51-53 | |
| SSH access | `rodeo ssh rancher`, `rodeo ssh k3s`, ... | |

Every VM boots from the openSUSE Leap 16 cloud image (no iPXE, no long install wait). Rancher installs via Helm on K3s. Then rodeo creates one custom cluster per downstream cluster in Rancher and runs its registration command on each node. Every node gets all three roles (etcd, control plane, worker). Rancher installs K3s or RKE2 on the nodes and the clusters show up in **Cluster Management** as Rancher-managed clusters, so you can upgrade or scale them from the UI.

Versions: Rancher Prime 2.15.2, cert-manager v1.21.2, K3s/RKE2 v1.36.4 (the newest Kubernetes Rancher 2.15.2 supports). Override them in the plan's `versions` block (`rancher`, `k3s`, `cert_manager`, `downstream_k3s`, `downstream_rke2`).

---

## Host requirements

| Resource | Minimum |
|----------|---------|
| OS | Linux with KVM and nested virt (if host is a VM) |
| RAM | ~18 GiB (`rancher-test`) or ~30 GiB (`rancher`) available |
| Disk | ~130 GiB (`rancher-test`) or ~220 GiB (`rancher`) free in `/var/lib/libvirt/images` |
| CPU | ~8 (`rancher-test`) or ~14 (`rancher`) vCPU to spare |
| Python | 3.10+ |

Run `rodeo doctor` to check your host and confirm this profile fits.

---

## Deploy

```bash
rodeo up --profile rancher        # or: rodeo up --profile rancher-test
```

`rodeo up` checks the host, installs any missing packages (with your consent), generates credentials, and starts the deploy. It self-escalates with sudo: you do not need to prefix `sudo` yourself.

`rodeo up` wraps itself in a tmux session (`rodeo-rancher`) automatically, so a dropped SSH connection does not kill the deploy. Re-attach with `tmux attach -t rodeo-rancher`. Use `--no-tmux` to skip this in scripts.

If `rodeo up` is already installed and you have already run `install-deps` once:

```bash
rodeo up --profile rancher --yes    # skip all prompts
```

### What happens during deploy

The pipeline runs these phases in order:

1. **kvm_host**: sets up libvirt, firewall rules, and the storage pool on the host
2. **vms**: downloads the cloud image, injects cloud-init, creates the VM disks and libvirt definitions
3. **boot**: starts the libvirt network and the VMs (no PXE wait needed)
4. **rancher**: waits for the Rancher VM to get an IP, installs K3s, deploys Rancher Prime via Helm, waits for the UI to become healthy
5. **downstream**: creates each downstream cluster in Rancher, registers its nodes, and waits until every cluster is Ready
6. **finalise**: enables VM autostart on host reboot (skipped on Instruqt; use `rodeo start-if-needed` on hostimage boot instead of baking finalise into the image)

Total time: about **20-40 minutes** on a typical host. Most of it is Rancher provisioning the downstream clusters; the 3-node RKE2 cluster joins one node at a time.

---

## Log in

After `rodeo up` finishes it prints the Rancher URL and credentials. If you need them again:

```bash
rodeo status
```

- **URL:** `https://<host-ip>:30002`
- **Username:** `admin`
- **Password:** the value of `rancher_admin_password` in `~/.rodeo/secrets.yaml`

First login will prompt you to confirm the server URL. Use the host IP (the one shown in `rodeo status`), not `localhost`.

---

## Day-2 operations

| Task | Command |
|------|---------|
| Check VM and service health | `rodeo status` |
| SSH into the Rancher VM | `rodeo ssh rancher` |
| SSH into a downstream node | `rodeo ssh k3s`, `rodeo ssh rke2-ha1`, ... |
| Tail serial log | `rodeo logs rancher` |
| Restart the VM | `rodeo restart rancher` |
| Graceful stop | `rodeo stop --all --yes` |
| Start after stop | `rodeo start --all --yes` |
| Destroy the lab | `rodeo clean --yes` |
| Full host reset | `rodeo clean --all --yes --secrets` |

### Resume a failed deploy

```bash
rodeo status                    # find the last failed phase
rodeo deploy --from rancher     # resume from that phase
```

---

## Instruqt workflow

Set `deployment_target: instruqt` in `rodeo-plan.yaml` before deploying so `finalise` is skipped. **Do not** bake `finalise` into a hostimage (autostart can hang the Instruqt agent / console). After deploy, follow the success-screen checklist, Save the hostimage, and put **`rodeo start-if-needed`** in the track setup script so the lab comes up on every boot. See [Instruqt example](examples/instruqt.md).

---

## Customize

All settings live in `~/.rodeo/profiles/rancher/` (if deployed via `--profile`) or your local lab dir.

Override resources at deploy time:

```bash
rodeo deploy -P resources.rancher.memory_mib=12288
rodeo deploy -P resources.rancher.vcpu=6
rodeo deploy -P resources.rke2-node.memory_mib=6144   # every RKE2 node
```

The clusters, and which VM joins which, are in `definition.yaml` (`downstream_clusters` and `nodes`).

To make a modified copy you can edit and redeploy:

```bash
rodeo new myrancher --from rancher
$EDITOR ~/.rodeo/profiles/myrancher/rodeo-plan.yaml
rodeo up --profile myrancher
```

Full format reference: [Create your own rodeo](custom-rodeos.md).
