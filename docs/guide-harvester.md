# Harvester HCI — profile guide

This guide covers the four Harvester profiles in rodeo-cli. All deploy **SUSE Virtualization (Harvester HCI)** as nested KVM VMs on a single Linux host. Pick the one that fits your host and workshop goals.

---

## Pick your profile

| Profile | Nodes | Rancher | etcd | RAM needed | Use when |
|---------|-------|---------|------|-----------|----------|
| `test` | 2 Harvester | No | 1 leader | ~36 GiB | Quick evaluation, tight host, no HA needed |
| `harvester-ha` | 3 Harvester | No | 3-member HA | ~52 GiB | Harvester workshop with full etcd HA, no Rancher |
| `harvester-2n` | 2 Harvester + 1 Rancher | Yes | 1 leader | ~56 GiB | HCI + Rancher on a mid-size host, no etcd HA |
| `harvester` | 3 Harvester + 1 Rancher | Yes | 3-member HA | ~60 GiB | Full lab: HCI + multi-cluster management |

Run `rodeo doctor` to see which profiles fit your host's available RAM.

---

## What you get

### All Harvester profiles

| Component | Default IP | Access |
|-----------|-----------|--------|
| Harvester VIP | 192.168.122.10 | `https://192.168.122.10` (direct, inside lab network) |
| Harvester UI via host | host IP | `https://<host>:8443` (DNAT, reachable from outside) |
| harvester1 | 192.168.122.11 | `rodeo ssh harvester1` |
| harvester2 | 192.168.122.12 | `rodeo ssh harvester2` |
| harvester3 | 192.168.122.13 | `rodeo ssh harvester3` (not present in `test` / `harvester-2n`) |

### `harvester` and `harvester-2n` profiles add

| Component | Default IP | Access |
|-----------|-----------|--------|
| Rancher Prime | 192.168.122.9 | `https://<host>:30002` (DNAT) |

---

## Host requirements

| Profile | RAM | Disk | CPU |
|---------|-----|------|-----|
| `test` | ~36 GiB | ~600 GiB in `/var/lib/libvirt/images` | ~16 vCPU |
| `harvester-ha` | ~52 GiB | ~1000 GiB | ~20 vCPU |
| `harvester` | ~72 GiB | ~1050 GiB | ~34 vCPU |

**OS:** Linux with KVM. SLES 16 or Leap 16 recommended; Ubuntu and Fedora work via `install-deps`. Nested virtualization must be enabled if the host is itself a VM (cloud, Instruqt).

Harvester installs via **iPXE network boot** (the `pxe_server` phase serves boot scripts and config over HTTP). This is why the disk requirement is high — each node gets a 320 GiB virtual disk (Harvester's Elemental installer carves a fixed 150 GiB persistent partition regardless of disk size, so the rest goes to Longhorn's own partition).

---

## Deploy

```bash
rodeo up --profile test           # 2-node, fastest
rodeo up --profile harvester-ha   # 3-node HA, no Rancher
rodeo up --profile harvester      # full lab
```

`rodeo up` checks the host, installs missing packages (with your consent), generates credentials, and drives the full pipeline. It self-escalates with sudo.

**Disconnect protection (tmux):** `rodeo up` automatically wraps itself in a named tmux session before starting the deploy. If your SSH or Instruqt connection drops mid-deploy, the process keeps running. Re-attach any time:

```bash
tmux attach -t rodeo-harvester    # or rodeo-test, rodeo-harvester-ha — matches --profile name
```

Detach without stopping the deploy: `Ctrl+b  d`. If you run `rodeo up` again and the session exists, it re-attaches to the running deploy instead of starting a second one. Use `--no-tmux` to skip this behaviour in scripts.

---

## What happens during deploy

The `suse-virt` pipeline (used by all four Harvester profiles) runs these phases:

1. **kvm_host** — prepares the hypervisor: KVM packages, libvirt daemon, firewall rules (including the DNAT rule that makes `:8443` reach the Harvester VIP), and the image storage pool
2. **vms** — downloads the Harvester ISO, creates virtual disks (size varies by profile — see Host requirements above), and writes libvirt XML definitions (VMs are defined but not started yet)
3. **pxe_server** — sets up nginx + TFTP + dnsmasq on the host's `virbr0` (192.168.122.1). Generates a `boot.ipxe` that chains to a per-node MAC script, and a Harvester config YAML for each node
4. **cluster** — starts VMs in order (`harvester1` first, then a gap for etcd, then the rest). Waits for each node to install Harvester via iPXE, join the cluster, and become `Ready`. Watches the VIP (192.168.122.10) for the cluster to converge
5. **rancher** — (`harvester` profile only) installs K3s + Rancher Prime on the `rancher` VM, exposes it on NodePort 30002, and configures the admin API. Harvester cluster import is intentionally left as a lab exercise — students do it via the Rancher UI as the first challenge
6. **finalise** — enables VM autostart on host reboot (skipped on Instruqt; use `rodeo start-if-needed` on hostimage boot instead of baking finalise into the image)

### Time estimates

| Profile | Typical time |
|---------|-------------|
| `test` | 45–90 minutes |
| `harvester-ha` | 60–120 minutes |
| `harvester` | 90–150 minutes |

Most of that time is the Harvester iPXE install inside nested KVM. Watch live progress:

```bash
rodeo watch              # split-panel TUI: phases + serial logs
rodeo logs harvester1    # just the serial log for one node
```

The TUI shows all VM serial consoles simultaneously in a vertical split. The left panel has a global elapsed timer for the full deploy. Each console window shows a per-VM elapsed timer that starts from when the first serial output arrives for that node — useful for spotting a node that started late or stopped responding.

---

## Log in

After deploy finishes, `rodeo up` prints the URLs and credentials. To see them again:

```bash
rodeo status
```

- **Harvester UI:** `https://<host>:8443`
- **Username:** `admin`
- **Password:** value of `harvester_admin_password` in `~/.rodeo/secrets.yaml`
- **Rancher UI** (harvester profile): `https://<host>:30002` — same password

The Harvester bootstrap process prompts for a VIP on first login if the cluster has not yet converged. If the UI asks for a VIP, use `192.168.122.10`.

> **Instruqt labs:** Harvester cluster import into Rancher is the first lab challenge. The infrastructure is ready; students complete the import via Rancher UI > Virtualization Management > Import Existing.

### Download the kubeconfig

From the Harvester UI: Support → Download KubeConfig.

Or via SSH tunnel to the VIP:

```bash
ssh -i ~/.ssh/your-key -N -L 6443:192.168.122.10:6443 user@<host>
# then in another terminal:
kubectl --kubeconfig ~/.rodeo/harvester-kubeconfig get nodes
```

The kubeconfig is also at `/root/.rodeo/harvester-kubeconfig` on the host after deploy.

---

## Day-2 operations

| Task | Command |
|------|---------|
| Check cluster and VM health | `rodeo status` |
| Watch live logs | `rodeo watch` |
| SSH into a node | `rodeo ssh harvester1` |
| Tail serial log | `rodeo logs harvester2` |
| Restart a single node | `rodeo restart harvester1` |
| Graceful stop (preserves VMs) | `rodeo stop --all --yes` |
| Start after stop | `rodeo start --all --yes` |
| Destroy the lab | `rodeo clean --yes` |
| Full host reset | `rodeo clean --all --yes --secrets` |
| Support bundle | `rodeo logs --bundle -o rodeo-bundle.tar.gz` |
| Rotate the admin password | `rodeo set-password` |
| Install/reconcile Rancher UI extensions | `rodeo install-extensions` |

### Resume a failed deploy

```bash
rodeo status                      # see which phase failed
rodeo deploy --from cluster       # resume from that phase
```

The deploy pipeline is idempotent within a phase. If the `cluster` phase times out waiting for a node, check the serial log (`rodeo logs harvester1`) for the root cause before resuming.

### Rancher UI extensions

Extensions (e.g. the SUSE Virtualization / Harvester extension) are declared once
under `rancher.ui_extensions` in `definition.yaml` — see the [full field
reference](reference/definition.md#rancherui_extensions) for the format. Every
deploy reconciles the declared list automatically; to install or upgrade one on an
already-deployed lab without redeploying, run:

```bash
rodeo install-extensions --yes
```

It talks straight to the running Rancher Prime over SSH/API, so it's safe to run
against a live cluster. Check what's actually installed first:

```bash
rodeo ssh rancher
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl -n cattle-ui-plugin-system get uiplugins.catalog.cattle.io harvester \
  -o jsonpath='{.spec.plugin.version}'
```

An empty result (or `NotFound`) means it isn't installed yet; a version string
that doesn't match `definition.yaml` means it's due for reconcile.

---

## Instruqt workflow

This is the workflow for building an **Instruqt hostimage** with a pre-deployed Harvester cluster. Full detail: [Instruqt example](examples/instruqt.md).

1. Set `deployment_target: instruqt` in `rodeo-plan.yaml` (skips `finalise` — keep it that way)
2. Deploy the lab (`rodeo up --profile harvester --yes` or `rodeo deploy`)
3. Verify: `rodeo status`
4. **Before Save:** do **not** run `rodeo deploy --finalise`; confirm agent ports `15778`/`15779` and that `libvirt-guests` is disabled (the success screen prints this checklist)
5. Save the hostimage in the Instruqt UI
6. Wire **`rodeo start-if-needed`** into the track setup script so every attendee boot starts VMs and re-applies DNAT/nft

Do not bake `finalise` into the hostimage — nested VM autostart races the Instruqt agent and can leave the console on **Please Wait**.

---

## Troubleshooting

### Harvester VIP not reachable after cluster phase

The cluster phase waits for the VIP (`192.168.122.10`) to respond on port 443. If it times out:

```bash
rodeo logs harvester1    # check the install log — is it still installing?
rodeo ssh harvester1     # if the node is up, check: kubectl get nodes
```

Common causes: disk too small (Harvester's Elemental installer always carves a fixed 150 GiB persistent partition — a smaller disk starves it and causes "no space" errors in containerd), nested virt not enabled, not enough RAM.

### UI reachable but host:8443 not working

The DNAT from `host:8443` to the Harvester VIP goes through libvirt's nftables firewall. If port forwarding is broken after a `virsh net-destroy`/restart:

```bash
sudo /etc/libvirt/hooks/network default started   # re-apply the DNAT rule
```

The hook is installed by the `kvm_host` phase. It re-runs automatically when libvirt restarts the default network.

### Node never joins the cluster

Check the serial log for the stuck node. Common issues:

- **HTTP 403 on iPXE config**: nginx couldn't read the config file (permissions). Re-run `rodeo deploy --from pxe_server`.
- **Wrong NIC name in config**: Harvester config uses `hwAddr` (MAC) to identify the interface, not `eth0`. This is handled automatically — if you see a NIC error, check the MAC in `definition.yaml` matches the VM.
- **etcd timeout on join**: there is a built-in gap (default 60 s) between starting harvester1 and starting the others. If etcd still times out, the first node may not have finished installing — check its log.

### etcd recovery (last resort)

If one etcd member is permanently broken (e.g. after accidentally destroying its VM), the three-step recovery is:

```bash
# 1. From a healthy node, remove the broken member
crictl exec <etcd-container-id> etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/var/lib/rancher/rke2/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/rke2/server/tls/etcd/server-client.crt \
  --key=/var/lib/rancher/rke2/server/tls/etcd/server-client.key \
  member remove <member-id>

# 2. On the broken node, wipe the etcd data and add a rejoin config
rm -rf /var/lib/rancher/rke2/server/db/etcd
cat > /etc/rancher/rke2/config.yaml.d/00-rejoin.yaml <<EOF
server: https://192.168.122.10:9345
token: <cluster-token>
EOF

# 3. Restart rke2-server on the broken node
systemctl restart rke2-server
```

The cluster token is `harvester_token` in `~/.rodeo/secrets.yaml`.

### Admin password unknown (not in secrets.yaml, not the persisted file, not admin/admin)

`rodeo set-password` recovers the admin password by trying known candidates — the
configured `secrets.yaml` value, the last one it persisted on the host, and the
`admin`/`admin` bootstrap. If none of those match (e.g. someone changed it by hand
outside of rodeo-cli and forgot), there's nothing left for it to log in *with*, so
it can't roll the password forward. Recover directly via `kubectl` instead — this
is the same technique Rancher documents for "locked out, don't know the current
password," and it applies here because Harvester's dashboard embeds the same
`users.management.cattle.io` user-management CRD (confirmed live: `cattle-system`
runs a full 3-replica `rancher` deployment fronting the Harvester UI/API).

```bash
# 1. Find the admin user's object name
export KUBECONFIG=/root/.rodeo/harvester-kubeconfig
kubectl get users.management.cattle.io \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.username}{"\n"}{end}'
# -> e.g. user-4wwtk admin

# 2. Generate a bcrypt hash of the new password (Go's bcrypt accepts $2a$/$2b$/$2y$ alike)
python3 -m pip install --quiet bcrypt
python3 -c "import bcrypt; print(bcrypt.hashpw(b'YourNewPassword123', bcrypt.gensalt(10)).decode())"

# 3. Patch the user object directly — sets the password hash and clears the
#    "must change password" flag in one shot
kubectl patch users.management.cattle.io <user-object-name> --type=merge \
  -p '{"password":"<hash-from-step-2>","mustChangePassword":false}'

# 4. Restart Rancher so all 3 replicas drop their stale in-memory cache of the
#    old hash — without this, kube-vip round-robins your login attempts across
#    replicas and you'll see intermittent 401s even though the patch landed in
#    etcd cleanly. Only touches cattle-system — no VM/storage/network impact.
kubectl -n cattle-system rollout restart deployment/rancher
kubectl -n cattle-system rollout status deployment/rancher --timeout=180s
```

Verify, then bring `secrets.yaml` and the persisted-candidate file back in sync so
this doesn't happen again on the next rotation:

```bash
curl -sk -X POST https://192.168.122.10/v3-public/localProviders/local?action=login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YourNewPassword123"}'
# -> a JSON body with a "token" field means it worked

# Update ~/.rodeo/secrets.yaml's harvester_admin_password to match, then:
echo -n 'YourNewPassword123' > /root/harvester-password
chmod 600 /root/harvester-password
```

If `htpasswd` (part of `apache2-utils`) is available instead of Python, `htpasswd
-bnBC 10 "" 'YourNewPassword123' | tr -d ':\n'` produces an equivalent hash.

---

## Customize

### Change node resources

Edit the profile files in `~/.rodeo/profiles/<profile>/` or override at deploy time:

```bash
rodeo deploy -P resources.harvester.memory_mib=20480
rodeo deploy -P resources.harvester.vcpu=10
```

### Change the disk size

The default is 320 GiB per Harvester node. Do not go below ~250 — Harvester's Elemental installer always carves a fixed 150 GiB persistent partition regardless of disk size, so smaller disks starve it and prevent container images from pulling, and leave little room for Longhorn itself.

```bash
rodeo deploy -P resources.harvester.disk_gb=400
```

### Build a custom Harvester lab

```bash
rodeo new edge-lab --from harvester-ha    # scaffold from 3-node HA
$EDITOR ~/.rodeo/profiles/edge-lab/definition.yaml
rodeo up --profile edge-lab
```

Full format reference: [Create your own rodeo](custom-rodeos.md).
