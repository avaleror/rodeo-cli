# Operations runbook

Quick reference for diagnosing and recovering from problems in a deployed rodeo lab. Each section starts with how to confirm the problem and then covers the fix.

---

## Deploy failed mid-pipeline

**Confirm:** `rodeo status` shows a red phase. The terminal output has an error message.

**Fix:** identify the failed phase and resume:

```bash
rodeo status                      # find the last failed phase
rodeo deploy --from <phase>       # resume from there
```

If you are not sure what changed, force a full re-run of the failed phase:

```bash
rodeo deploy --from kvm_host --force   # re-run everything from phase kvm_host
```

---

## Harvester install appears stuck — stuck vs timed out

**Context:** the `cluster` phase waits up to 60 minutes for the Harvester VIP to come up. On nested KVM this can take 45–90 minutes. The question is whether the install is still making progress or has genuinely hung.

**Step 1 — check the heartbeat file:**

```bash
cat ~/.rodeo/logs/<lab-name>-heartbeat.txt
```

rodeo writes this file every 5 minutes during the VIP and node-ready waits. It records VM states and elapsed time. If the file exists and the timestamp is recent (< 6 min old), the deploy is alive. If the file is missing or stale, the host process may have died.

**Step 2 — check VM states:**

```bash
virsh list --all
```

- All `running` = install in progress (normal). Watch `rodeo logs harvester1` for kernel/installer output.
- All `shut off` = installer exited early. Check the serial log for the error:

```bash
rodeo logs harvester1
```

- Any `paused` = memory pressure on the host. Free RAM or add swap, then `virsh resume <vm>`.

**Step 3 — distinguish a stuck install from an Instruqt session timeout:**

Instruqt terminates the GCP lab VM (not just the browser session) after the platform-configured idle timeout. If the lab VM is gone, there is no reconnecting — the install cannot have "hung" because the host no longer exists.

Signs the lab was terminated (not stuck):
- `virsh list --all` fails with "failed to connect to the hypervisor"
- SSH to the Instruqt host times out entirely
- The heartbeat file is missing or from a previous run

Signs the install is genuinely stuck:
- VMs are `running`, heartbeat is fresh, but serial log has not scrolled for > 15 minutes
- Serial log shows a repeating error (disk full, interface not found, etcd election loop)

**Common install hang causes:**

| Symptom in serial log | Cause | Fix |
|---|---|---|
| `no network interface found` or hangs at network config | Interface name mismatch — installer got a config naming `eth0` but kernel uses `ens3` | Wipe VMs (`rodeo deploy --from vms --force`) and redeploy |
| `curl: (7) Failed to connect` to config URL | nginx not running or virbr0 not up | Check nginx: `rodeo ssh <host> -- sudo systemctl status nginx` |
| Disk full / `containerd` errors | VM disk too small (Elemental's persistent partition floor is a fixed 150 GiB) | Redeploy with `disk_gb: 320` in plan |
| `etcd` election loop, node never joins | Rapid join race on 3-node setup | Increase `etcd_join_gap_seconds` in `definition.yaml` (default: 90) |

---

## Harvester VIP not responding

**Symptom:** the `cluster` phase times out waiting for `192.168.122.10:443`.

**Confirm:**

```bash
rodeo logs harvester1   # is the installer still running?
```

If the log is still progressing (you see kernel messages or the Harvester installer), wait — it can take 45–90 minutes on nested KVM.

If the log is stuck or empty:

```bash
rodeo ssh harvester1              # connect to the node
sudo journalctl -u rke2-server -f # check RKE2 status
```

**Common causes and fixes:**

| Cause | Fix |
|-------|-----|
| Disk too small | Clean and redeploy with `disk_gb: 320` in the plan |
| Not enough RAM | `rodeo doctor` to check; free up RAM or use a smaller profile |
| `pxe_server` served wrong config | Check nginx logs: `rodeo ssh <host> -- sudo journalctl -u nginx` |
| etcd join race | The 90s `etcd_join_gap_seconds` prevents this; check if it was reduced |

---

## Host port-forward not working (host:8443 / host:30002)

**Symptom:** `https://<host>:8443` times out from outside, but `https://192.168.122.10:443` works from inside the host.

**Confirm:**

```bash
# On the host:
sudo nft list table ip libvirt_network   # look for the guest_input chain
```

If the `guest_input` chain has a `reject` rule but no `ct status dnat accept` rule before it, the libvirt hook is missing or did not fire.

**Fix:**

```bash
sudo /etc/libvirt/hooks/network default started   # re-apply the DNAT rule
```

If the hook file is missing (e.g. after a `kvm_host` phase that did not complete):

```bash
rodeo deploy --from kvm_host --force   # reinstalls the hook
```

---

## Secrets not resolved

**Symptom:** deploy fails immediately with `unresolved credential` or `empty password`.

**Fix:**

```bash
# Regenerate secrets
rodeo init --force
# Then retry deploy
rodeo deploy --from kvm_host --force
```

Or edit `~/.rodeo/secrets.yaml` directly and add the missing keys.

---

## "python3-lxml not found" error

**Symptom:** the `vms` Ansible phase fails with a missing `lxml` module error.

**Fix:**

```bash
sudo rodeo install-deps   # installs python3-lxml alongside other host packages
```

Then resume:

```bash
rodeo deploy --from vms
```

---

## VM stuck in `paused` state

**Confirm:**

```bash
virsh list --all   # shows STATE = paused
```

**Fix:**

```bash
virsh resume <vm-name>
# or via rodeo:
rodeo restart <vm-name>
```

If it re-pauses immediately, check host RAM — the hypervisor pauses VMs when memory pressure is critical.

---

## etcd member broken (one node lost)

This happens if a Harvester VM was force-deleted or its disk corrupted while the cluster was running.

**Confirm from a healthy node:**

```bash
rodeo ssh harvester2
# Inside the VM:
ETCD_CERT=/var/lib/rancher/rke2/server/tls/etcd
crictl exec $(crictl ps --name etcd -q) etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=$ETCD_CERT/server-ca.crt \
  --cert=$ETCD_CERT/server-client.crt \
  --key=$ETCD_CERT/server-client.key \
  member list
```

If one member shows `unstarted` or is missing:

**Recovery (3 steps):**

```bash
# Step 1: remove the broken member from a healthy node
MEMBER_ID=<id from member list>
crictl exec <etcd-container-id> etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=$ETCD_CERT/server-ca.crt \
  --cert=$ETCD_CERT/server-client.crt \
  --key=$ETCD_CERT/server-client.key \
  member remove $MEMBER_ID
```

```bash
# Step 2: on the broken node, wipe the etcd data and add a rejoin config
rodeo ssh harvester1   # or whichever node is broken
sudo rm -rf /var/lib/rancher/rke2/server/db/etcd
sudo tee /etc/rancher/rke2/config.yaml.d/00-rejoin.yaml <<EOF
server: https://192.168.122.10:9345
token: $(grep harvester_token ~/.rodeo/secrets.yaml | awk '{print $2}')
EOF
```

```bash
# Step 3: restart rke2-server on the broken node
sudo systemctl restart rke2-server
# Wait ~3 minutes, then verify:
kubectl get nodes   # all should be Ready
```

---

## libvirt not starting

**Symptom:** `virsh list` fails with a connection error.

**Confirm:**

```bash
systemctl status virtqemud.socket virtnetworkd.socket
```

SLES 16 uses **modular libvirt** (socket-activated daemons), not the monolithic `libvirtd`. If the sockets are inactive:

```bash
sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket
```

---

## virbr0 missing after host reboot

**Symptom:** VMs cannot start because `virbr0` does not exist.

**Cause:** `libvirt-guests` was not enabled (the `finalise` phase was skipped or failed), so libvirt did not start automatically.

**Fix:**

```bash
sudo virsh net-start default
sudo virsh net-autostart default
```

Or re-run finalise:

```bash
rodeo deploy --from finalise --finalise
```

---

## Accidentally destroyed the virbr0 network with virsh

**Symptom:** ran `virsh net-destroy default` while the cluster was running. All VM network connectivity is lost.

**Fix (restore connectivity without VM restart):**

```bash
# Re-create the bridge and reattach tap devices
sudo ip link set virbr0 up
for tap in $(virsh domiflist harvester1 | awk '/vnet/{print $1}') \
           $(virsh domiflist harvester2 | awk '/vnet/{print $1}') \
           $(virsh domiflist harvester3 | awk '/vnet/{print $1}'); do
    sudo ip link set $tap master virbr0
    sudo ip link set $tap up
done
sudo ip neigh flush dev virbr0

# Restart the libvirt network (re-applies DNAT rules and firewall hook)
sudo virsh net-start default
sudo /etc/libvirt/hooks/network default started
```

Verify connectivity:

```bash
ping -c 3 192.168.122.11   # harvester1
ping -c 3 192.168.122.10   # VIP
```

If VMs lost their cluster state, you may need to recover etcd (see above).

---

## Full host reset

Use this when you want to start over completely — wipes all lab VMs, disks, state, and credentials, but leaves packages and the rodeo binary:

```bash
rodeo stop --all --yes 2>/dev/null || true   # graceful stop first
rodeo clean --all --yes --secrets --force-network
```

Then deploy fresh:

```bash
rodeo up --profile <name>
```

---

## Collect a support bundle

```bash
rodeo logs --bundle -o rodeo-bundle.tar.gz
```

The bundle includes: redacted plan (secrets replaced with `***`), phase state, recent serial log tails for all VMs, and `virsh` domain info. Attach it when filing a bug or escalating to maintainers.
