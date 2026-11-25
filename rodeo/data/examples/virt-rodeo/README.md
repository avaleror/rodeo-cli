# virt-rodeo profile

The SUSE Virtualization Rodeo
([suse-virt-rodeo](https://github.com/SUSE-Technical-Marketing/suse-virt-rodeo))
on our own cloud hosts instead of Instruqt. One host per student, deployed by
`rodeo fleet`, with the per-lab player (`rodeo/player/`) running the track's
chapter scripts.

Infra is the same as [`virt-workshop`](../virt-workshop/README.md): 3-node
Harvester HCI + Rancher Prime, `m8id.8xlarge` on AWS, and the same
`custom/scripts/` for the pre-lab state the track expects (image cache, NFS
backup target, student kit, pre-created `webserver-prod`).

What is different:

| | virt-workshop | virt-rodeo |
|---|---|---|
| Self-checks | `checks/check-exercise-N.sh` | none: the track's `check-kvm-host` scripts are the checks |
| `/srv/backups` | plain directory on `/` | own ext4 filesystem in a sparse loop file, mounted `nosuid,nodev,noexec` |

## Why the NFS export changed

Chapter 6 points Harvester's backup target at `192.168.122.1:/srv/backups/`,
exported `rw,no_root_squash` (Longhorn writes backups as root). The student
kubeconfig is cluster-admin, so a student can run a privileged pod on a
Harvester node, write a setuid-root binary into the export, and run it as
`student` on the host. Mounting the export `nosuid,nodev,noexec` stops that
without changing what Harvester sees.

`60-nfs-backup-target.sh`:

- creates `/var/lib/rodeo-nfs/backups.ext4` (sparse, 200G by default;
  `RODEO_NFS_BACKUP_IMG` and `RODEO_NFS_BACKUP_SIZE` override) once;
- moves the contents of an older plain `/srv/backups` into it once;
- mounts it and writes the `/etc/fstab` line once;
- remounts its own mount if the options were lost;
- refuses to remount or export a `/srv/backups` it did not create unless that
  mount already has all three options;
- checks the live options again right before `exportfs -ra`.

## Usage

```bash
rodeo up --profile virt-rodeo --target aws --yes
rodeo fleet up --profile virt-rodeo --count 10
```

`virt-workshop` and `virt-workshop-2n` are unchanged.
