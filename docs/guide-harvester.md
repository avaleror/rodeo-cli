# Harvester HCI — profile guide

This guide covers the three Harvester profiles in rodeo-cli. All three deploy **SUSE Virtualization (Harvester HCI)** as nested KVM VMs on a single Linux host. Pick the one that fits your host and workshop goals.

---

## Pick your profile

| Profile | Nodes | Rancher | etcd | RAM needed | Use when |
|---------|-------|---------|------|-----------|----------|
| `test` | 2 Harvester | No | 1 leader | ~36 GiB | Quick evaluation, tight host, no HA needed |
| `harvester-ha` | 3 Harvester | No | 3-member HA | ~52 GiB | Harvester workshop with full etcd HA, no Rancher |
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
| harvester3 | 192.168.122.13 | `rodeo ssh harvester3` (not present in `test`) |

### `harvester` profile adds

| Component | Default IP | Access |
|-----------|-----------|--------|
| Rancher Prime | 192.168.122.9 | `https://<host>:30002` (DNAT) |

---

## Host requirements

| Profile | RAM | Disk | CPU |
|---------|-----|------|-----|
| `test` | ~36 GiB | ~600 GiB in `/var/lib/libvirt/images` | ~16 vCPU |
| `harvester-ha` | ~52 GiB | ~800 GiB | ~20 vCPU |
| `harvester` | ~60 GiB | ~900 GiB | ~28 vCPU |

**OS:** Linux with KVM. SLES 16 or Leap 16 recommended; Ubuntu and Fedora work via `install-deps`. Nested virtualization must be enabled if the host is itself a VM (cloud, Instruqt).

Harvester installs via **iPXE network boot** (the `pxe_server` phase serves boot scripts and config over HTTP). This is why the disk requirement is high — each node gets a 250 GiB virtual disk (Harvester needs at least that for its persistent partition and image storage).

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

The `suse-virt` pipeline (used by all three Harvester profiles) runs these phases:

1. **kvm_host** — prepares the hypervisor: KVM packages, libvirt daemon, firewall rules (including the DNAT rule that makes `:8443` reach the Harvester VIP), and the image storage pool
2. **vms** — downloads the Harvester ISO, creates virtual disks (250 GiB each), and writes libvirt XML definitions (VMs are defined but not started yet)
3. **pxe_server** — sets up nginx + TFTP + dnsmasq on the host's `virbr0` (192.168.122.1). Generates a `boot.ipxe` that chains to a per-node MAC script, and a Harvester config YAML for each node
4. **cluster** — starts VMs in order (`harvester1` first, then a gap for etcd, then the rest). Waits for each node to install Harvester via iPXE, join the cluster, and become `Ready`. Watches the VIP (192.168.122.10) for the cluster to converge
5. **rancher** — (`harvester` profile only) installs K3s + Rancher Prime on the `rancher` VM, waits for Rancher to become healthy, then imports the Harvester cluster
6. **finalise** — enables VM autostart on host reboot (skipped on Instruqt until you run `--finalise`)

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
- **Password:** value of `lab_admin_password` in `~/.rodeo/secrets.yaml`
- **Rancher UI** (harvester profile): `https://<host>:30002` — same password

The Harvester bootstrap process prompts for a VIP on first login if the cluster has not yet converged. If the UI asks for a VIP, use `192.168.122.10`.

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

### Resume a failed deploy

```bash
rodeo status                      # see which phase failed
rodeo deploy --from cluster       # resume from that phase
```

The deploy pipeline is idempotent within a phase. If the `cluster` phase times out waiting for a node, check the serial log (`rodeo logs harvester1`) for the root cause before resuming.

---

## Instruqt workflow

This is the workflow for building an **Instruqt track image** with a pre-deployed Harvester cluster.

1. Set `deployment_target: instruqt` in `rodeo-plan.yaml`
2. Deploy the lab:
   ```bash
   rodeo deploy
   ```
3. Verify the cluster is healthy:
   ```bash
   rodeo status
   ```
4. Take the Instruqt snapshot via the Instruqt UI or API
5. After the snapshot, enable VM autostart for attendee instances:
   ```bash
   rodeo deploy --from finalise --finalise
   ```

Without step 5, VMs do not autostart when an attendee instance boots, and the cluster will not be available.

---

## Troubleshooting

### Harvester VIP not reachable after cluster phase

The cluster phase waits for the VIP (`192.168.122.10`) to respond on port 443. If it times out:

```bash
rodeo logs harvester1    # check the install log — is it still installing?
rodeo ssh harvester1     # if the node is up, check: kubectl get nodes
```

Common causes: disk too small (must be 250 GiB — smaller causes "no space" errors in containerd), nested virt not enabled, not enough RAM.

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

---

## Customize

### Change node resources

Edit the profile files in `~/.rodeo/profiles/<profile>/` or override at deploy time:

```bash
rodeo deploy -P resources.harvester.memory_mib=20480
rodeo deploy -P resources.harvester.vcpu=10
```

### Change the disk size

The default is 250 GiB per Harvester node. Do not go below 250 — smaller disks fill the persistent partition and prevent container images from pulling.

```bash
rodeo deploy -P resources.harvester.disk_gb=300
```

### Build a custom Harvester lab

```bash
rodeo new edge-lab --from harvester-ha    # scaffold from 3-node HA
$EDITOR ~/.rodeo/profiles/edge-lab/definition.yaml
rodeo up --profile edge-lab
```

Full format reference: [Create your own rodeo](custom-rodeos.md).
