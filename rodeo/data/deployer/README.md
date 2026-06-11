# Agnostic deployer

Deploys the SUSE Virtualization Rodeo stack — a 3-node Harvester 1.8.0 cluster
plus Rancher Prime 2.13.1 (on K3s), all as nested KVM guests — on **any**
SLES 16 / openSUSE Leap 16 host: bare metal, a cloud VM, or an IaaS instance.

No Instruqt required. The Instruqt builder track lives in `../builder/` and is
left untouched; this deployer reuses the same shared Ansible roles in
`../ansible/` and runs the same VM-start and Rancher-import logic standalone.

## What you need

A SLES 16 (or Leap 16 / openSUSE) host with:

- Nested virtualization enabled (`kvm_intel.nested=1` / `kvm_amd.nested=1`)
- Enough capacity: 32 vCPU, ~90 GB RAM, ~950 GB disk for the full stack
  (3 × Harvester at 8 vCPU / 20 GiB / 270 GB + Rancher at 4 vCPU / 12 GiB / 60 GB =
  28 vCPU / 72 GiB; disks are thin, ~300-350 GB actually used). On a 128 GiB host
  you can raise the node memory in `ansible/roles/vms/defaults/main.yml`.
- Outbound internet (pulls the Harvester ISO, Leap image, K3s, Rancher charts, and
  the Kubernetes RPM repo for kubectl)
- Just `ansible` installed up front (`sudo zypper in ansible`). The playbook installs
  the rest: KVM stack, `xorriso`, `jq`/`curl`, `firewalld`, and `kubectl` — for which
  it adds the upstream Kubernetes repo (`pkgs.k8s.io`, channel `stable:/v1.36`), since
  kubectl is not in the SUSE base repos. SSH to the guests is key-based — the playbook
  generates a host key and bakes the public key into the Rancher VM and the Harvester
  nodes, so no `sshpass`/password.

## Run it

```bash
cd deployer
cp deploy.env.example deploy.env
$EDITOR deploy.env          # set passwords, pick nat or bridge
sudo ./deploy.sh
```

`deploy.sh` then:

1. Installs the Ansible collections (`../ansible/requirements.yml`).
2. Runs `../ansible/playbook.yml` (kvm_host + vms roles) to configure the host
   and stage every VM asset — disks, Harvester config ISOs, Rancher cloud-init,
   and the libvirt domains.
3. Starts the VMs and waits for the Harvester cluster to form (`lib/start-vms.sh`).
4. Installs K3s + Rancher Prime and imports the Harvester cluster
   (`lib/setup-rancher.sh`). Skip with `SKIP_RANCHER=true`.

Expect 40–90 minutes end to end; most of it is the Harvester install.

After deploy completes, the lab DNS names are live on the host (via `/etc/hosts`):

| Name | IP |
|------|----|
| `virtualization.aerogrid.com` | 192.168.122.10 (Harvester VIP) |
| `rancher.aerogrid.com` | 192.168.122.9 (Rancher Prime) |
| `alpha.aerogrid.com` | 192.168.122.11 (Harvester node 1) |
| `bravo.aerogrid.com` | 192.168.122.12 (Harvester node 2) |
| `charlie.aerogrid.com` | 192.168.122.13 (Harvester node 3) |

VMs on the NAT network resolve the same names via the libvirt dnsmasq on `192.168.122.1`.
Kubernetes pods inside Harvester resolve them via a forward zone patched into RKE2's CoreDNS.

## Networking: nat vs bridge

Set `NETWORK_MODE` in `deploy.env`.

- **`nat`** (default) — guests sit on libvirt's `192.168.122.0/24` NAT network.
  The host exposes the Harvester UI (`:8443`) and Rancher (`:30002`) and DNATs
  them to the guests via firewalld port-forwarding. Self-contained; works on any
  single host with no LAN planning.
- **`bridge`** — guests attach to an existing host bridge and get real LAN IPs.
  Create the bridge first (e.g. `nmcli con add type bridge ifname br0 ...`), then
  set `NETWORK_MODE=bridge`, `HARVESTER_VIP`, and `RANCHER_IP` in `deploy.env`, and
  copy `deploy.vars.yml.example` to `deploy.vars.yml` for the LAN gateway and the
  per-node IP list. `deploy.env` stays authoritative for mode/VIP/IPs, so keep the
  two files consistent (harvester1's IP = `HARVESTER_VIP`, rancher's = `RANCHER_IP`).
  No DNAT; reach the UIs directly on the VIP. Note: all five Harvester NICs land on
  the same bridge, so the storage/migration/service split is logical only — fine for
  a lab, not a substitute for real per-network isolation.

## Files

| File | Purpose |
|------|---------|
| `deploy.sh` | Single entrypoint — orchestrates everything. |
| `deploy.env.example` | Copy to `deploy.env` (gitignored); passwords + knobs. |
| `deploy.vars.yml.example` | Copy to `deploy.vars.yml` (gitignored); Ansible overrides for bridge mode / custom IPs. |
| `inventory.local` | Default inventory — targets the local host. |
| `lib/start-vms.sh` | Starts the domains, waits for Harvester. |
| `lib/setup-rancher.sh` | K3s + Rancher Prime install and Harvester import. |

## Remote target

By default the deployer runs against the host it is invoked on
(`inventory.local`, `ansible_connection=local`). To drive a remote host, point
`ANSIBLE_INVENTORY` at your own inventory file. Note that `lib/start-vms.sh` and
`lib/setup-rancher.sh` use local `virsh`/`ssh`, so for a remote target run the
deployer on that host (e.g. over SSH) rather than from a separate control node.
