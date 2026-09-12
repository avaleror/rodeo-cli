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
| `70-webserver-prod.sh` | Creates the `prod` namespace, node labels (`stage=prod` on harvester1/2, `stage=dev` on harvester3 — the same "one non-candidate node" setup suse-virt-rodeo uses for a real live-migration contrast), the `prod/service` VM network, and the `webserver-prod` VM itself (1 vCPU / 1 GiB / 5 GiB, from the cached image) | suse-virt-workshop's current Exercise 4.1, where the student creates this VM themselves |

All three are idempotent (check-then-create) and re-run on every `rodeo up` —
see `docs/custom-rodeos.md` for the phase's guarantees.

**Known risk:** the `prod/service` VM network's exact shape (bridge on
`mgmt-br`, untagged/vlan 0) follows Harvester's standard "VM Network on the
management network" convention, but suse-virt-rodeo's own repo has this
baked into its Instruqt image rather than scripted anywhere — there was no
proven-working manifest to copy from. Live-verify VM connectivity after a
fresh deploy of this profile.

```bash
rodeo up --yes --no-tmux --profile virt-workshop-aws --target aws
```

Tear down with `rodeo destroy --cloud --yes` — the cached image and NFS
export live on the host's own NVMe/root disk and are destroyed with the
instance, nothing persists elsewhere.
