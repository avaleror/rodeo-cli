# SUSE Telco Cloud 3.7: profile guide

`--profile suse-telco` builds the infrastructure for the SUSE Telco Cloud 3.7 workshop (Northline). rodeo stands up the management cluster, an emulated Redfish BMC, the downstream image, and enrolls the site hosts. It stops there. Students apply the workshop's `manifests/` themselves.

The bundled plan and the copy in [avaleror/suse-telco-workshop](https://github.com/avaleror/suse-telco-workshop) must stay identical. `rodeo up --target aws` seeds the EC2 host from the copy in this repo.

---

## What you get

| VM | Default IP | Role |
|---|---|---|
| mgmt | 192.168.122.10 | RKE2 management cluster, Rancher |
| site-co | 192.168.122.21 | Site host. Metal3 powers it |
| site-ran | 192.168.122.32 | Site host |
| site-co-2 | 192.168.122.23 | Site host |
| site-co-3 | 192.168.122.24 | Site host |

DNS domain: `northline.telco`. Gateway: `192.168.122.1`.

Guest sizes in the bundled plan: mgmt 8 vCPU / 16 GiB / 80 GiB disk, each site host 4 vCPU / 8 GiB / 40 GiB disk.

**Component versions (SUSE Telco Cloud 3.7.0 release notes):** Rancher 2.15.1 · RKE2 v1.36.3+rke2r1 · cert-manager v1.20.1 · Turtles providers chart 307.0.8+up0.27.0 · Metal3 chart 307.0.31+up0.16.0 · MetalLB 307.0.3+up0.16.1 · Longhorn 1.12.1 · Edge Image Builder 1.3.4. Helm components are chart versions. KubeVirt (chart 307.0.3+up0.8.0) is for lab 07.

---

## Editions

`edition` in the plan picks the base image and the Rancher chart repo. The Telco Cloud pins stay the same.

| `edition` | Built from | Who fetches the base image |
|---|---|---|
| `product` (default) | SUSE Linux Micro 6.2 + Rancher Prime | You. Stage `SL-Micro.x86_64-6.2-Base-GM.raw.xz`, or set `telco.base_image.url` |
| `opensource` | openSUSE Leap Micro 6.2 + community Rancher | rodeo downloads it |

Neither edition needs an SCC registration code. If `credentials.scc_registration_code` is set on the product edition, the downstream image is built from SUSE repositories instead of the public SLE BCI repo.

On `--target aws` the instance's NVMe is mounted over `/var/lib/libvirt/images`, so a file you copied onto the host before `rodeo up` is hidden. Set `telco.base_image.url` (a presigned link in the same region works).

---

## Deploy

```bash
rodeo up --profile suse-telco
rodeo up --profile suse-telco --target aws --yes
rodeo up --profile suse-telco --target gcp --yes
```

Bare metal is the plan default. `--target` wins and is written back into the plan.

The catalog's recommended cloud size is a starting point, not a measured pin: AWS `m8id.4xlarge` (16 vCPU / 64 GiB / local NVMe) for labs 01–06. The performance tier is the metal size, for nested KubeVirt in lab 07. GCP recommended is `c4-standard-16-lssd`.

---

## What rodeo runs

1. **kvm_host**: KVM, libvirt, firewall, storage
2. **vms**: libvirt network, the mgmt disk from the SL Micro or Leap Micro image plus a combustion seed, blank site disks, domain XML
3. **bmc**: sushy-tools, the emulated Redfish BMC for the site hosts
4. **boot**: starts mgmt only. Metal3 owns power for the site hosts
5. **mgmt**: installs the management stack
6. **images**: builds the downstream image with EIB on the KVM host and publishes it to the mgmt image cache
7. **enroll**: registers the site hosts as BareMetalHosts and waits until they are available
8. **finalise** and **custom_scripts**

The lab is ready for students after enroll. `finalise` and `custom_scripts` still run after that. `finalise` is skipped when `deployment_target` is `instruqt`, same as the other profiles. Applying `manifests/` is the student's job. In the workshop repo that starts with `kubectl apply -f manifests/site-co.yaml` (lab 04).
