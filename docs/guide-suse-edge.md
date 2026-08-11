# SUSE Edge 3.6 — profile guide

This guide covers the `suse-edge` profile: a full **SUSE Edge 3.6** stack on nested KVM. It stands up a Rancher Prime management cluster with the **Elemental Operator**, an **Edge Image Builder (EIB)** VM, and a set of Elemental-managed **edge nodes** — the pieces you need to demo zero-touch edge onboarding end to end.

Use it when the workshop or demo focuses on **edge lifecycle**: building an OS image with EIB, then registering edge nodes into Rancher over TPM with Elemental.

---

## What you get

| Component | Default IP | Access |
|-----------|-----------|--------|
| Rancher Prime (management cluster) | 192.168.122.9 | `https://rancher.<host-ip>.sslip.io` (Traefik + Let's Encrypt) |
| Edge Image Builder (EIB) | 192.168.122.20 | `rodeo ssh eib` |
| edge1 | 192.168.122.31 | `rodeo ssh edge1` |
| edge2 | 192.168.122.32 | `rodeo ssh edge2` |
| edge3 | 192.168.122.33 | `rodeo ssh edge3` |
| edge4 | 192.168.122.34 | `rodeo ssh edge4` |

Rancher is reached through **Traefik ingress**, not a NodePort: cert-manager gets a real Let's Encrypt certificate over the HTTP01 challenge (port 80), and the UI + API + Elemental registration endpoint are served on port 443 at a `sslip.io` hostname derived from the host's external IP. Edge nodes are created by the `vms` phase but **started as a lab exercise**, not automatically — that is the hands-on part of the workshop.

**Component versions (SUSE Edge 3.6):** Rancher Prime 2.14.1 · K3s v1.35.5+k3s1 (lab; production uses RKE2 v1.35.3+rke2r3) · cert-manager v1.20.1 · Elemental Operator 1.9.0 · Edge Image Builder 1.3.3.1.

---

## Host requirements

| Resource | Minimum |
|----------|---------|
| OS | Linux with KVM and nested virt (if host is a VM) |
| RAM | ~40 GiB available |
| Disk | ~200 GiB free in `/var/lib/libvirt/images` |
| CPU | ~8 vCPU to spare |
| Extras | `swtpm` for the virtual TPM 2.0 on edge nodes (installed by `install-deps`) |
| Python | 3.10+ |

The host also needs inbound **ports 80 and 443** reachable from where you browse, so Let's Encrypt can complete the ACME challenge and you can reach the Rancher UI.

Run `rodeo doctor` to check your host and confirm this profile fits.

---

## Deploy

```bash
rodeo up --profile suse-edge
```

`rodeo up` checks the host, installs any missing packages (with your consent), generates credentials, and starts the deploy. It self-escalates with sudo — you do not need to prefix `sudo` yourself.

`rodeo up` wraps itself in a tmux session (`rodeo-suse-edge`) automatically, so a dropped SSH connection does not kill the deploy. Re-attach with `tmux attach -t rodeo-suse-edge`. Use `--no-tmux` to skip this in scripts.

### What happens during deploy

The pipeline runs these phases in order:

1. **kvm_host** — sets up libvirt, firewall rules (including the 80/443 DNAT), swtpm, and the storage pool on the host
2. **vms** — downloads the base images, injects cloud-init, creates the Rancher, EIB, and edge node disks and libvirt definitions
3. **boot** — starts the libvirt network and the Rancher + EIB VMs (edge nodes stay off for the lab exercise)
4. **rancher** — installs K3s, deploys Rancher Prime via Helm, and configures cert-manager + Traefik for the Let's Encrypt certificate
5. **elemental** — installs the Elemental Operator (CRDs + Operator) and creates the MachineRegistration so edge nodes can register over TPM
6. **apply** — applies any extra manifests (Fleet GitOps, demo workloads)
7. **finalise** — enables VM autostart on host reboot (skipped on Instruqt; use `rodeo start-if-needed` on hostimage boot instead of baking finalise into the image)

Total time: **20–40 minutes** on a typical host, depending on image download speed.

---

## Log in

After `rodeo up` finishes it prints the Rancher URL and credentials. If you need them again:

```bash
rodeo status
```

- **URL:** `https://rancher.<host-ip>.sslip.io`
- **Username:** `admin`
- **Password:** the value of `rancher_admin_password` in `~/.rodeo/secrets.yaml`

Because Rancher is served with a real Let's Encrypt certificate, there is no browser warning to click through — but the host must be reachable on ports 80 and 443 from the public internet for the certificate to issue.

---

## The edge lab exercise

The edge nodes are the point of this profile. After the deploy finishes:

1. On the **EIB** VM, build an Elemental OS image (SelfInstall ISO for edge1/edge2, Default RAW for edge3/edge4).
2. Start an edge node and boot it from that image: `rodeo restart edge1`, then watch it with `rodeo logs edge1`.
3. The node registers into Rancher over its virtual TPM via the Elemental MachineRegistration.
4. Watch it appear under **Cluster Management → Machine Inventory** in the Rancher UI.

Tune how many registrations exist with `elemental.registrations` in the plan.

---

## Day-2 operations

| Task | Command |
|------|---------|
| Check VM and service health | `rodeo status` |
| SSH into a VM | `rodeo ssh rancher` / `rodeo ssh eib` / `rodeo ssh edge1` |
| Tail serial log | `rodeo logs edge1` |
| Start an edge node | `rodeo restart edge1` |
| Graceful stop | `rodeo stop --all --yes` |
| Start after stop | `rodeo start --all --yes` |
| Destroy the lab | `rodeo clean --yes` |
| Full host reset | `rodeo clean --all --yes --secrets` |

### Resume a failed deploy

```bash
rodeo status                      # find the last failed phase
rodeo deploy --from elemental     # resume from that phase
```

---

## Instruqt workflow

Set `deployment_target: instruqt` in `rodeo-plan.yaml` before deploying so `finalise` is skipped. **Do not** bake `finalise` into a hostimage (autostart can hang the Instruqt agent / console). After deploy, follow the success-screen checklist, Save the hostimage, and put **`rodeo start-if-needed`** in the track setup script so the lab comes up on every boot. See [Instruqt example](examples/instruqt.md).

---

## Customize

All settings live in `~/.rodeo/profiles/suse-edge/` (if deployed via `--profile`) or your local lab dir.

Override resources or edge behaviour at deploy time:

```bash
rodeo deploy -P resources.rancher.memory_mib=12288
rodeo deploy -P rancher_tls.email=me@example.com
rodeo deploy -P elemental.registrations=2
```

To make a modified copy you can edit and redeploy:

```bash
rodeo new myedge --from suse-edge
$EDITOR ~/.rodeo/profiles/myedge/rodeo-plan.yaml
rodeo up --profile myedge
```

Full format reference: [Create your own rodeo](custom-rodeos.md).
