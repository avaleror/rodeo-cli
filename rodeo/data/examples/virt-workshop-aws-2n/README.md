# virt-workshop-aws-2n profile

Budget-tier AWS variant of [`virt-workshop-aws`](../virt-workshop-aws/README.md): 2-node Harvester HCI + Rancher Prime (no HA — same topology as `harvester-2n`) instead of 3 nodes, sized for a smaller `m8id.4xlarge` (16 vCPU / 64 GiB / 950 GB NVMe, ~$1.11/hr) instead of `m8id.8xlarge` (~$2.22/hr).

Exists because the 3-node profile's flat AWS disk floor (500 GB/Harvester-node) doesn't fit `m8id.4xlarge`'s smaller NVMe device: `2 x 500 + 60 (Rancher) = 1060 GB` is more than the device's 950 GB. This profile's `rodeo-plan.yaml` sets `resources.harvester.disk_floor_override_gb: 400`, opting it out of the global floor (`2 x 400 + 60 = 860 GB`, fits with ~90 GB margin) — every other AWS profile is unaffected, the override is opt-in per plan.

## Why 2 nodes doesn't lose any exercise

Checked [suse-virt-workshop](https://github.com/avaleror/suse-virt-workshop)'s 8 exercises before building this: none of them reference a dedicated "3rd, excluded" node. suse-virt-rodeo's own chapter 4 (live migration) labels `harvester3` as `stage=dev` purely as an internal implementation detail for a "one non-candidate node" flavor of the story — no exercise text asks the student to inspect or rely on that label. `custom/scripts/70-webserver-prod.sh` here drops it entirely: both nodes are `stage=prod`, and the live-migration story (migrate `webserver-prod` from one node to the other) is if anything more natural with exactly two.

## What's different from virt-workshop-aws

| | `virt-workshop-aws` | `virt-workshop-aws-2n` |
|---|---|---|
| Topology | 3-node Harvester (etcd HA) + Rancher | 2-node Harvester (no etcd HA) + Rancher, same as `harvester-2n` |
| Instance | `m8id.8xlarge` (32 vCPU / 128 GiB / 1900 GB NVMe) | `m8id.4xlarge` (16 vCPU / 64 GiB / 950 GB NVMe) |
| Per-node sizing | 24 GiB / 10 vCPU / 500 GB | 16 GiB / 8 vCPU / 400 GB (`disk_floor_override_gb: 400`) |
| Node labels | `stage=prod` on harvester1/2, `stage=dev` on harvester3 | `stage=prod` on harvester1/2 only — no dev label |
| `custom/scripts/` | 50/60 identical; 70 labels 3 nodes | 50/60 identical; 70 labels 2 nodes, otherwise the same |

## Deploy

```bash
rodeo up --yes --no-tmux --profile virt-workshop-aws-2n --target aws
```

Tear down with `rodeo destroy --cloud --yes` — same as the 3-node profile, nothing persists outside the instance.
