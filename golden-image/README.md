# Golden image for rodeo-cli labs at scale

A platform-agnostic way to deploy rodeo labs on many hosts (workshops, not
Instruqt) without depending on any single cloud. The portable unit is a **disk
image plus a first-boot config**, which every platform can boot: AWS, GCP,
Azure, Equinix, VMware, and bare metal.

This directory is the working area for that path. It is kept on the
`feat/golden-image` branch and rebased onto `main` automatically (see
[Branch sync](#branch-sync)).

## Why this exists

rodeo-cli is a run-on-the-host, single-host tool. You do not scale it by making
it multi-host. You run one rodeo instance per host and give every host the same
pre-staged starting point, so 50 parallel deploys do not each download 8 GB and
take an hour.

## The layered model

Do not bake one monolithic image. The Harvester ISO alone is 7.7 GB, and rodeo
moves faster than the OS, so a single image would be huge and would rot.

```
  Layer 1  base OS image        KIWI (Leap 16) + cloud-init
           small, stable, rebuilt rarely, one build per platform format
                     |
                     v
  Layer 2  rodeo + heavy blobs  first-boot cloud-init: install rodeo, pull the
           fast-moving, ~8 GB   Harvester ISO and base images from a mirror
                                 near the hosts, then run one profile
```

Layer 1 lives here (`kiwi/`). Layer 2 is a first-boot step, so the big blobs and
the fast-moving rodeo bits stay out of the base image.

## What is validated

Tested live on a SLES 16 host:

- **KIWI 10.2.33 builds a bootable cloud-init qcow2** from the public Leap 16 OSS
  repo in about 2.5 minutes, with no subscription. See `kiwi/leap16-base.kiwi`.
- **Cloud-format coverage**: KIWI emits native one-shot images for GCP (`gce`)
  and Azure (`vhdfixed`), plus `vmdk`/`ova` for VMware and `qcow2`/raw for KVM
  and bare metal.
- **cloud-init, not combustion**, is the right first-boot mechanism. rodeo
  already standardizes on cloud-init, including its SUSE Edge path.

## Known constraints

- **AWS has no one-shot format.** Build raw, then run `import-snapshot` and
  register the AMI. This is the one place a build tool like Packer is more
  direct.
- **A SLES base needs a subscription** at build time (tokenized repos). A Leap
  16 base uses public repos and avoids that, and Leap is already the base rodeo
  uses for its guests.
- **Nested virtualization** is a property of the host instance, not the image.
  It constrains instance selection on every platform regardless of build tool.

## Build

```bash
kiwi-ng system build \
  --description golden-image/kiwi \
  --target-dir /var/tmp/rodeo-golden-out
```

Change the `type` `format` in `kiwi/leap16-base.kiwi` to target another
platform (`gce`, `vhdfixed`, `vmdk`, `ova`).

## Branch sync

`feat/golden-image` is a long-lived branch, kept ready to merge into `main`.

- A GitHub Action (`.github/workflows/sync-golden-image.yml`, once merged to
  `main`) merges `main` into this branch on every push to `main`. On a merge
  conflict it stops and opens an issue instead of leaving the branch broken.
- Sync uses merge, not rebase, because the branch is squash-merged into `main`,
  so a linear history buys nothing and merge avoids force-push. A plain
  `git pull` on a local checkout always works.

## Next step

A layered proof of concept: this Leap base plus a first-boot cloud-init that
installs rodeo and runs one profile end to end on a fresh cloud host.
