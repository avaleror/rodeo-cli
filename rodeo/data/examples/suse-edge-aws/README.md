# suse-edge-aws profile

Same topology as the bare-metal `suse-edge` profile (Rancher Prime + Elemental + EIB + 4 edge nodes with vTPM, SUSE Edge 3.6) — pre-tuned for AWS instead of a bare-metal KVM host.

## What's different from `suse-edge`

| | `suse-edge` (bare metal) | `suse-edge-aws` |
|---|---|---|
| Rancher | 8 GiB / 4 vCPU / 60 GB | 12 GiB / 4 vCPU / 60 GB |
| EIB | 12 GiB / 4 vCPU / 100 GB | 16 GiB / 4 vCPU / 150 GB |
| Edge node ×4 | 4 GiB / 2 vCPU / 20 GB | 4 GiB / 2 vCPU / 25 GB |
| Guest total | ~40 GiB / 16 vCPU / 250 GB | ~44 GiB / 16 vCPU / 310 GB |
| Rancher TLS | `letsEncrypt` (sslip.io + ACME) | `secret` (self-signed, NodePort) — no external DNS/ACME dependency |

Resource math sized from scratch for this profile's real footprint (much smaller than Harvester's), not copied from `harvester-aws`. `letsEncrypt` was designed for a directly-reachable public IP (Instruqt, or AWS) and would technically work here too, but `secret` keeps the profile dependency-free and matches `virt-workshop-aws`'s choice.

## Instance tiers

| Tier | Instance | vCPU | RAM | Local storage | ~$/hr (eu-north-1, 2026-09-14) |
|---|---|---|---|---|---|
| `budget` | `m7i.4xlarge` | 16 | 64 GiB | EBS only | $0.86 |
| `recommended` | `m8id.4xlarge` | 16 | 64 GiB | 1× 950 GB NVMe | $1.11 |
| `performance` | `m7i.metal-24xl` | 96 | 384 GiB | EBS only (bare metal) | $5.14 |

Unlike `harvester-aws`/`virt-workshop-aws`, `budget` here is a genuine budget pick: same vCPU/RAM as `recommended`, just no local NVMe, and actually cheaper — this profile's guest footprint (~44 GiB RAM / 16 vCPU) fits a 4xlarge-class instance from the start, so there was no need to jump to a bigger/pricier EBS-only box the way the 3-node Harvester profiles did.

## Deploy

```bash
rodeo up --yes --no-tmux --profile suse-edge-aws --target aws
```

Fill in `provider.region`/`provider.subnet_id` first. Tear down with `rodeo destroy --cloud --yes`.

**Not yet live-verified end to end** (as of 2026-09-14) — the bare-metal `suse-edge` profile has been extensively validated (EIB raw-image quirks, Hauler airgap, Gitea, TPM registration all fixed and working), but this is the first AWS-specific variant. Live-verify before relying on it for a real workshop; watch especially for: vTPM (`swtpm`) behavior under nested KVM on EC2 (untested combination), and whether EIB's guestfish/raw-image handling behaves the same on the AWS host's NVMe-backed storage as it did on bare-metal SLES.
