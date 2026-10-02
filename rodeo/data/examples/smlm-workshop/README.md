# smlm-workshop — SUSE Multi-Linux Hands-on Workshop

This lab rebuilds the Instruqt track
[SUSE-Technical-Marketing/instruqt-SMLM](https://github.com/SUSE-Technical-Marketing/instruqt-SMLM)
(`tracks/smlms`, branch `main`) as nested VMs on one KVM host. The VM names
match the track's machines, so its eight challenges apply unchanged.

The responsibilities are split. rodeo prepares the host: packages, libvirt,
firewall, secrets and the network. [lab-in-a-box](https://github.com/SUSE-Technical-Marketing/lab-in-a-box)
creates the VMs and configures SUSE Multi-Linux Manager from the `smlm`
section of `rodeo-plan.yaml`. That section is the declarative version of the
track's `setup-smlm` and `setup-zbastion` scripts.

| VM | Role | Base image |
|---|---|---|
| `smlm` | SMLM server (podman), web UI on host port 443 | pre-built (`bake/`) |
| `centos7`, `zzcentos7` | RES 7 / Liberty clients | CentOS 7 GenericCloud 2211 |
| `sles15` | SLES 15 SP5 client (upgrade exercise) | SLES15-SP5 Minimal VM Cloud |
| `zzsles15a/b/c` | SLES 15 SP6 clients | SLES15-SP6 Minimal VM Cloud |
| `ubuntu2404lts` | Ubuntu client | Ubuntu 24.04 cloud image |

It needs about 30 GiB of RAM and about 450 GB of disk.

## Before the first deploy

1. **Build the SMLM server image** with `bake/`, once per refresh (see
   `bake/README.md`). It starts from SUSE's SMLM Server **BYOS** image, registered
   with your own subscription code. The result stays private in your account. You
   get an image URL and sha256 (or, for AWS, an AMI ID) plus the admin password it
   was built with.
2. **Have a download URL and sha256 for the SLES base images**
   `SLES15-SP5-Minimal-VM.x86_64-Cloud-GM.qcow2` and
   `SLES15-SP6-Minimal-VM.x86_64-Cloud-GM.qcow2`. They need a SUSE account, so the
   URL is yours to give: an authenticated or mirror `https://` URL, or
   `file:///path` for a copy already on the host.

Every base image is downloaded by lab-in-a-box itself, into its `ISO_LOC` on
the KVM host, the first time it is needed. Each one is checked against its
sha256 before use, and a verified copy is reused by later deploys. The CentOS
and Ubuntu images need no input.

## Without a pre-built image

`scratch` builds the SMLM server on the deploying host itself, from SUSE's SMLM
Server BYOS qcow2 registered with your code. It installs SMLM and syncs every
channel from SCC, which takes hours.

Set `variant: scratch` under `lab_in_a_box:` in `rodeo-plan.yaml`, then run
`rodeo up`, which asks for the values this variant needs.

It needs your SMLM registration code, SCC mirror credentials and the BYOS image's
URL/sha256 instead of the pre-built image values. The deploy finishes
before the sync does; the `zz*` clients register by themselves once their
channels are ready.

## In an AWS account

`variant: aws` creates every VM in an AWS account instead of on nested KVM:
- the SMLM server boots the private AMI from `bake/` (its `aws` variant);
- the clients boot AMIs you pick in your region.

Set `variant: aws`, then `rodeo up` asks for the AWS credentials, a security group
and the AMI IDs.

## Deploy

```bash
rodeo up --profile smlm-workshop
```

`rodeo up` generates this lab's own passwords (`universal_pwd`,
`smlm_admin_password`). It asks once for the image values from steps 1 and 2
and stores everything in `~/.rodeo/secrets.yaml`. No credential is written to the
plan or to git.

After the deploy, `./workshop` holds the track, `./workshop-guide/` the challenges
with this lab's values filled in, and — when pandoc and Chromium are installed —
`./workshop-guide.pdf`. The success screen shows
the values the assignments refer to (`SMLM_URL`, `SMLM_USERNAME`,
`UNIVERSAL_PWD`). Students log in to the SMLM web UI as `myadmin` with
`UNIVERSAL_PWD`. It is also the root password of every client VM.

## Several copies on one host

```bash
rodeo instances new smlm-workshop --count 3      # ~/rodeo-labs/smlm-workshop-1..3
rodeo up --dir ~/rodeo-labs/smlm-workshop-1       # then -2, -3
rodeo instances list                              # subnets, host ports, deployed?
```

Each copy gets:
- its own network (`192.168.(122+N).0/24`) and its own passwords;
- its own web UI port: `443 + N×1000`, so instance 1's SMLM is on host port 1443.

The SMLM server keeps its image's name, `smlm.rodeo.lab`, inside every copy's own
network, so one pre-built image serves all of them.

## One copy per host, with fleet

`rodeo fleet deploy` works with this profile. Name the operator secrets in
`workshop.yaml` and fleet copies them to each host; see
[docs/fleet.md](../../../../docs/fleet.md#labs-with-operator-secrets-or-other-uis).

## Pre-registered clients

As in the track, the `zz*` machines are already registered in SMLM when the lab
starts, under the system names the exercises use:

| VM | SMLM system | Activation key |
|---|---|---|
| `zzsles15a` | `at-ct-pro` | `1-sles15sp6` |
| `zzsles15b` | `at-ft-pro` | `1-sles15sp6` |
| `zzsles15c` | `at-ct-qa` | `1-sles15sp6` |
| `zzcentos7` | `airco-dh4a-prod` | `1-liberty7ltss` |

`custom/scripts/10-smlm-config.sh` then puts them into their system groups and
sets their `application` values. The students register `centos7` (as
`airco-dh4a-qa`), `sles15` and `ubuntu2404lts` themselves during the challenges.

## Known gaps

- Only checked offline so far. On the first real deploy, confirm that the
  pre-built SMLM server accepts running at another instance's IP under its fixed
  name `smlm.rodeo.lab` (instances ≥ 1).
- `zz*` client prep (`custom/scripts/20-client-prep.sh`) only works once a client
  is registered; if registration is still pending, it catches up on the next
  `rodeo up`.
- `centos7`/`zzcentos7` keep CentOS's default SSH setting, as in the track:
  students work in them through their terminals (`rodeo ssh centos7`), and no
  exercise logs in with a password.
