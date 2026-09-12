# virt-workshop-aws profile

Same 3-node Harvester HCI + Rancher Prime topology and AWS sizing as
`harvester-aws` (`m8id.8xlarge`, 24 GiB/Harvester-node, 16 GiB Rancher, 500 GB/
Harvester-node disk floor) — plus the extra pre-lab state
[suse-virt-workshop](https://github.com/avaleror/suse-virt-workshop)'s
exercises need, so a student can run the same story
[suse-virt-rodeo](https://github.com/avaleror/suse-virt-rodeo) runs on
Instruqt from a pre-built image, on a from-scratch AWS deploy instead.

`custom/scripts/` (see [Custom rodeos: `custom/scripts/`](../../../../docs/custom-rodeos.md#custom-scripts--a-custom_scripts-phase-after-finalise)
for how the phase itself works):

| Script | What it does | Replaces |
|---|---|---|
| `50-image-cache.sh` | Downloads openSUSE Leap 16.0's small KVM cloud image (~308 MiB, freely redistributable — SLES's equivalent is gated behind SUSE Customer Center) and serves it over HTTP on the libvirt gateway (`192.168.122.1:8889`) via a systemd unit | suse-virt-rodeo's manually-placed image cache |
| `60-nfs-backup-target.sh` | Exports `/srv/backups` over NFS to `192.168.122.0/24` — the exact endpoint (`192.168.122.1:/srv/backups/`) Exercise 6's backup-target setting expects | suse-virt-workshop's current Exercise 6.5, where the *student* hand-rolls this per-distro (with an escape hatch: "if it's blocked in your environment, move on") |
| `70-webserver-prod.sh` | Creates the `prod` namespace, node labels (`stage=prod` on harvester1/2, `stage=dev` on harvester3 — the same "one non-candidate node" setup suse-virt-rodeo uses for a real live-migration contrast), the `prod/service` VM network, and both `webserver-prod` and `daily-batch-processor` (1 vCPU / 1 GiB each, boot disk sized dynamically from the cached image's real virtual size) — the batch VM is first pinned to webserver-prod's own node for a guaranteed collision, then released, matching suse-virt-rodeo's own script | suse-virt-workshop's current Exercise 4.1, where the student creates both VMs themselves |

All three are idempotent (check-then-create) and re-run on every `rodeo up` —
see `docs/custom-rodeos.md` for the phase's guarantees.

The `prod/service` VM network's shape (bridge on `mgmt-br`, untagged/vlan 0,
following Harvester's standard "VM Network on the management network"
convention) had no proven-working manifest to copy from anywhere in either
source repo — suse-virt-rodeo bakes it into its Instruqt image rather than
scripting it. Live-verified 2026-09-12 on a fresh `m8id.8xlarge` deploy:
`webserver-prod` reached `Running`/`LIVE-MIGRATABLE: True` and got a real
DHCP lease (`192.168.122.x`) from the default libvirt network, confirmed both
via the VMI's `.status.interfaces` and `virsh net-dhcp-leases default`.

The boot disk's PVC size is read live from the cached image's
`.status.virtualSize` (openSUSE Leap 16.0's KVM image: ~308 MiB download, 24
GiB virtual size) plus a 1 GiB buffer, floored at 5Gi — a fixed small size
(e.g. 5Gi) leaves the PVC permanently `Pending` and the VM
`ErrorUnschedulable`, since Longhorn/Harvester can't provision a volume
smaller than the image it's cloned from.

```bash
rodeo up --yes --no-tmux --profile virt-workshop-aws --target aws
```

Tear down with `rodeo destroy --cloud --yes` — the cached image and NFS
export live on the host's own NVMe/root disk and are destroyed with the
instance, nothing persists elsewhere.
