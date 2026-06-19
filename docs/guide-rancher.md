# Rancher Prime on K3s — profile guide

This guide covers the `rancher` profile: a single VM running **Rancher Prime on K3s**, no Harvester. It is the smallest lab in rodeo-cli (~10 GiB RAM) and the fastest to deploy (minutes, not hours).

Use it when the workshop or demo focuses on **Rancher multi-cluster management** rather than Harvester HCI.

---

## What you get

| Component | Default IP | Port |
|-----------|-----------|------|
| Rancher Prime (Rancher UI) | 192.168.122.9 | 30002 (NodePort) |
| Rancher UI via host DNAT | host IP | 30002 |
| SSH access | `rodeo ssh rancher` | |

The VM boots from a cloud image (Leap 16 or SLES 16 — no iPXE, no long install wait). Rancher installs via Helm on K3s and becomes reachable a few minutes after the VM starts.

---

## Host requirements

| Resource | Minimum |
|----------|---------|
| OS | Linux with KVM and nested virt (if host is a VM) |
| RAM | ~10 GiB available |
| Disk | ~80 GiB free in `/var/lib/libvirt/images` |
| CPU | ~4 vCPU to spare |
| Python | 3.10+ |

Run `rodeo doctor` to check your host and confirm this profile fits.

---

## Deploy

```bash
rodeo up --profile rancher
```

`rodeo up` checks the host, installs any missing packages (with your consent), generates credentials, and starts the deploy. It self-escalates with sudo — you do not need to prefix `sudo` yourself.

`rodeo up` wraps itself in a tmux session (`rodeo-rancher`) automatically, so a dropped SSH connection does not kill the deploy. Re-attach with `tmux attach -t rodeo-rancher`. Use `--no-tmux` to skip this in scripts.

If `rodeo up` is already installed and you have already run `install-deps` once:

```bash
rodeo up --profile rancher --yes    # skip all prompts
```

### What happens during deploy

The pipeline runs these phases in order:

1. **kvm_host** — sets up libvirt, firewall rules, and the storage pool on the host
2. **vms** — downloads the cloud image, injects cloud-init, creates the VM disk and libvirt definition
3. **boot** — starts the libvirt network and the VM (no PXE wait needed)
4. **rancher** — waits for the VM to get an IP, installs K3s, deploys Rancher Prime via Helm, waits for the UI to become healthy
5. **finalise** — enables VM autostart on host reboot (skipped on Instruqt until you run `--finalise`)

Total time: **5–15 minutes** on a typical host.

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

Set `deployment_target: instruqt` in `rodeo-plan.yaml` before deploying. The `finalise` phase is then skipped automatically until you run it after taking the Instruqt snapshot:

```bash
rodeo deploy --from finalise --finalise
```

This prevents the VM from trying to autostart before the Instruqt agent is ready on the attendee instance.

---

## Customize

All settings live in `~/.rodeo/profiles/rancher/` (if deployed via `--profile`) or your local lab dir.

Override resources at deploy time:

```bash
rodeo deploy -P resources.rancher.memory_mib=12288
rodeo deploy -P resources.rancher.vcpu=6
```

To make a modified copy you can edit and redeploy:

```bash
rodeo new myrancher --from rancher
$EDITOR ~/.rodeo/profiles/myrancher/rodeo-plan.yaml
rodeo up --profile myrancher
```

Full format reference: [Create your own rodeo](custom-rodeos.md).
