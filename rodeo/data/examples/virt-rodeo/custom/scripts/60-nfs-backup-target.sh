#!/bin/bash
# virt-rodeo custom_scripts step 2/4: the NFS backup target, hardened.
#
# Exports /srv/backups over NFS on the host, matching the endpoint
# suse-virt-rodeo's chapter 6 (The Unthinkable Error) hard-codes for
# Harvester's off-cluster backup target: 192.168.122.1:/srv/backups/
# (192.168.122.1 is the libvirt NAT gateway, the host itself).
#
# Differs from virt-workshop's copy in one way: /srv/backups is its own
# ext4 filesystem in a sparse loop file, mounted nosuid,nodev,noexec.
# The export stays rw,no_root_squash because Longhorn writes backups as
# root. Without this mount, root in a privileged pod on a Harvester node
# (the student kubeconfig is cluster-admin) could write a setuid-root binary
# into the export and the host's unprivileged `student` user could run it.
# A plain bind mount was not enough: the same inode stays reachable through
# the parent filesystem's own path, and du/space accounting is shared with /.
#
# Idempotent (this is a no_cache_phase): the loop file is created once,
# existing data in a plain /srv/backups directory is migrated into it once,
# the fstab line is written once, and a mount already in place is checked,
# not recreated. The script refuses to remount a /srv/backups it did not
# create, and refuses to export unless the three options are live.
#
# Package and unit names differ by host OS (SLES 16 / Leap 16, Ubuntu
# 22.04+, Fedora): detect the package manager rather than assuming zypper.

set -uo pipefail

log(){ echo ">>> [nfs-backup] $*"; }

BACKUP_DIR="/srv/backups"
EXPORT_CIDR="192.168.122.0/24"
EXPORT_LINE="${BACKUP_DIR} ${EXPORT_CIDR}(rw,sync,no_subtree_check,no_root_squash)"

# In the VM image directory, not on the root disk: on AWS that directory is
# the NVMe pool (~1.9 TiB), while the root EBS volume is small and one VM
# backup from chapter 6 could fill it.
BACKUP_IMG="${RODEO_NFS_BACKUP_IMG:-/var/lib/libvirt/images/rodeo-nfs/backups.ext4}"
BACKUP_SIZE="${RODEO_NFS_BACKUP_SIZE:-200G}"   # sparse: only what is written uses disk
MOUNT_OPTS="loop,nosuid,nodev,noexec"
# nofail: a missing image must not hang the host's boot. requires-mounts-for:
# mount the NVMe pool (its own fstab line) before the image that lives on it.
FSTAB_LINE="${BACKUP_IMG} ${BACKUP_DIR} ext4 ${MOUNT_OPTS},nofail,x-systemd.requires-mounts-for=$(dirname "${BACKUP_IMG}") 0 0"

fail(){ echo ">>> [nfs-backup] FAILED: $*" >&2; exit 1; }

# True when every one of nosuid, nodev, noexec is in the live mount options.
mount_is_hardened(){
  local opts
  opts="$(findmnt -no OPTIONS --mountpoint "${BACKUP_DIR}" 2>/dev/null)" || return 1
  for o in nosuid nodev noexec; do
    case ",${opts}," in *",${o},"*) ;; *) return 1 ;; esac
  done
}

# The loop device backing /srv/backups, if the mount is ours.
mount_is_ours(){
  local src back
  src="$(findmnt -no SOURCE --mountpoint "${BACKUP_DIR}" 2>/dev/null)" || return 1
  [ "${src}" = "${BACKUP_IMG}" ] && return 0
  case "${src}" in
    /dev/loop*)
      back="$(losetup -nO BACK-FILE "${src}" 2>/dev/null | sed 's/[[:space:]]*$//')"
      [ "${back}" = "${BACKUP_IMG}" ] ;;
    *) return 1 ;;
  esac
}

# Stop exporting the path while its filesystem changes under it.
unexport(){ exportfs -u "${EXPORT_CIDR}:${BACKUP_DIR}" 2>/dev/null || true; }

if [ ! -f "${BACKUP_IMG}" ]; then
  log "creating sparse ${BACKUP_SIZE} ext4 image at ${BACKUP_IMG} ..."
  install -d -m 700 "$(dirname "${BACKUP_IMG}")"
  truncate -s "${BACKUP_SIZE}" "${BACKUP_IMG}" || fail "truncate ${BACKUP_IMG}"
  chmod 600 "${BACKUP_IMG}"
  mkfs.ext4 -q -F -L rodeo-backups "${BACKUP_IMG}" || fail "mkfs.ext4 ${BACKUP_IMG}"
fi

if mountpoint -q "${BACKUP_DIR}"; then
  if ! mount_is_ours; then
    # Someone else's filesystem: never remount or export it from here.
    mount_is_hardened || fail "${BACKUP_DIR} is a mount this script did not create and it lacks nosuid,nodev,noexec; refusing to remount or export it"
    log "${BACKUP_DIR} is an existing hardened mount, leaving it as is"
  elif ! mount_is_hardened; then
    log "remounting ${BACKUP_DIR} nosuid,nodev,noexec ..."
    unexport
    mount -o remount,nosuid,nodev,noexec "${BACKUP_DIR}" || fail "remount ${BACKUP_DIR}"
  fi
else
  unexport
  mkdir -p "${BACKUP_DIR}"
  if [ -n "$(ls -A "${BACKUP_DIR}" 2>/dev/null)" ]; then
    # A plain directory from an older run: move its data into the image once.
    log "migrating existing ${BACKUP_DIR} contents into ${BACKUP_IMG} ..."
    STAGE="$(mktemp -d /run/rodeo-nfs-migrate.XXXXXX)"
    mount -o "${MOUNT_OPTS}" "${BACKUP_IMG}" "${STAGE}" || fail "mount ${BACKUP_IMG} for migration"
    cp -a "${BACKUP_DIR}/." "${STAGE}/" || { umount "${STAGE}"; fail "copy ${BACKUP_DIR} into the image"; }
    umount "${STAGE}" && rmdir "${STAGE}"
    find "${BACKUP_DIR}" -mindepth 1 -delete || fail "empty the old ${BACKUP_DIR}"
  fi
  log "mounting ${BACKUP_IMG} on ${BACKUP_DIR} (${MOUNT_OPTS}) ..."
  mount -o "${MOUNT_OPTS}" "${BACKUP_IMG}" "${BACKUP_DIR}" || fail "mount ${BACKUP_IMG} on ${BACKUP_DIR}"
fi

if mount_is_ours && ! grep -qxF "${FSTAB_LINE}" /etc/fstab; then
  log "persisting the mount in /etc/fstab ..."
  grep -v "[[:space:]]${BACKUP_DIR}[[:space:]]" /etc/fstab > /etc/fstab.rodeo-new || true
  echo "${FSTAB_LINE}" >> /etc/fstab.rodeo-new
  mv /etc/fstab.rodeo-new /etc/fstab
fi

mount_is_hardened || fail "${BACKUP_DIR} is not mounted nosuid,nodev,noexec; refusing to export it"
chmod 777 "${BACKUP_DIR}"  # lab-grade only; nothing on it can run or escalate

if ! systemctl list-unit-files nfs-server.service &>/dev/null \
  && ! systemctl list-unit-files nfs-kernel-server.service &>/dev/null; then
  log "installing NFS server package ..."
  if command -v zypper &>/dev/null; then
    zypper --non-interactive install nfs-kernel-server 2>/dev/null \
      || zypper --non-interactive install nfs-utils \
      || { echo ">>> [nfs-backup] FAILED to install an NFS server package (zypper)" >&2; exit 1; }
  elif command -v apt-get &>/dev/null; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nfs-kernel-server \
      || { echo ">>> [nfs-backup] FAILED to install an NFS server package (apt)" >&2; exit 1; }
  elif command -v dnf &>/dev/null; then
    dnf install -y -q nfs-utils \
      || { echo ">>> [nfs-backup] FAILED to install an NFS server package (dnf)" >&2; exit 1; }
  else
    echo ">>> [nfs-backup] FAILED: no known package manager (zypper/apt-get/dnf) found" >&2
    exit 1
  fi
fi

# The systemd unit name also differs: nfs-server (SLES/Fedora) vs
# nfs-kernel-server (Ubuntu/Debian) — try both, non-fatal if one is absent.
NFS_UNIT=""
for candidate in nfs-server nfs-kernel-server; do
  systemctl list-unit-files "${candidate}.service" &>/dev/null && NFS_UNIT="${candidate}" && break
done
[ -n "${NFS_UNIT}" ] || { echo ">>> [nfs-backup] FAILED: no nfs-server/nfs-kernel-server unit found after install" >&2; exit 1; }

if ! grep -qxF "${EXPORT_LINE}" /etc/exports 2>/dev/null; then
  log "writing /etc/exports entry for ${BACKUP_DIR} -> ${EXPORT_CIDR} ..."
  # Replace any stale entry for the same path (e.g. a previous run with a
  # different CIDR/options) rather than accumulating duplicates.
  if [ -f /etc/exports ]; then
    grep -v "^${BACKUP_DIR} " /etc/exports > /etc/exports.new || true
    mv /etc/exports.new /etc/exports
  fi
  echo "${EXPORT_LINE}" >> /etc/exports
fi

systemctl enable --now "${NFS_UNIT}" \
  || { echo ">>> [nfs-backup] FAILED to start ${NFS_UNIT}" >&2; exit 1; }

mount_is_hardened || fail "${BACKUP_DIR} lost nosuid,nodev,noexec before export"
exportfs -ra \
  || { echo ">>> [nfs-backup] FAILED to (re)export ${BACKUP_DIR}" >&2; exit 1; }

log "verifying export ..."
if exportfs -v | grep -q "^${BACKUP_DIR}"; then
  log "${BACKUP_DIR} exported to ${EXPORT_CIDR} — backup-target endpoint is 192.168.122.1:${BACKUP_DIR}/"
else
  echo ">>> [nfs-backup] FAILED: ${BACKUP_DIR} not present in 'exportfs -v' after export" >&2
  exit 1
fi

exit 0
