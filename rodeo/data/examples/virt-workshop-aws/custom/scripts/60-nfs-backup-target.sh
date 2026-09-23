#!/bin/bash
# suse-virt-workshop custom_scripts step 2/3.
#
# Exports /srv/backups over NFS on the host, matching the exact endpoint
# suse-virt-rodeo's chapter 6 (The Unthinkable Error) assignment hard-codes
# for Harvester's off-cluster backup-target setting: 192.168.122.1:/srv/backups/
# (192.168.122.1 is the libvirt NAT gateway — the host itself, already
# reachable from harvester1-3 with no extra networking).
#
# This replaces this workshop's old Exercise 6.5, where the STUDENT was
# asked to hand-roll this per-distro ("exact packages differ... if NFS setup
# is blocked in your environment, read the setting UI and move on") — fragile
# and inconsistent by design. Automating it here makes the step reliable
# instead of a coin flip, and matches Exercise 4's automation for consistency.
#
# Package name differs by host OS (this workshop supports SLES 16 / Leap 16,
# Ubuntu 22.04+, and Fedora per the README) — detect the package manager
# rather than assuming zypper.
#
# Idempotent: safe to re-run (this is a no_cache_phase) — checks before
# writing /etc/exports and only re-exports if the entry changed.

set -uo pipefail

log(){ echo ">>> [nfs-backup] $*"; }

BACKUP_DIR="/srv/backups"
EXPORT_CIDR="192.168.122.0/24"
EXPORT_LINE="${BACKUP_DIR} ${EXPORT_CIDR}(rw,sync,no_subtree_check,no_root_squash)"

mkdir -p "${BACKUP_DIR}"
chmod 777 "${BACKUP_DIR}"  # lab-grade only

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
