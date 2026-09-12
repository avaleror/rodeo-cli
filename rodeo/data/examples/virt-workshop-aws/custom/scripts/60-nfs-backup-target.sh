#!/bin/bash
# virt-workshop-aws custom_scripts step 2/3.
#
# Exports /srv/backups over NFS on the host, matching the exact endpoint
# suse-virt-rodeo's chapter 6 (The Unthinkable Error) assignment hard-codes
# for Harvester's off-cluster backup-target setting: 192.168.122.1:/srv/backups/
# (192.168.122.1 is the libvirt NAT gateway — the host itself, already
# reachable from harvester1-3 with no extra networking).
#
# This replaces suse-virt-workshop's current chapter 6.5, where the STUDENT
# is asked to hand-roll this ("Exact packages differ by distro... If NFS
# setup is blocked in your environment, read the setting UI and move on") —
# fragile and inconsistent by design. Automating it here makes that step
# reliable instead of a coin flip.
#
# Idempotent: safe to re-run (this is a no_cache_phase) — checks before
# writing /etc/exports and only re-exports if the entry changed.

set -uo pipefail

log(){ echo ">>> [nfs-backup] $*"; }

BACKUP_DIR="/srv/backups"
EXPORT_CIDR="192.168.122.0/24"
EXPORT_LINE="${BACKUP_DIR} ${EXPORT_CIDR}(rw,sync,no_subtree_check,no_root_squash)"

mkdir -p "${BACKUP_DIR}"
chmod 777 "${BACKUP_DIR}"  # lab-grade only — matches suse-virt-workshop's own existing note

# SLES 16 / openSUSE package name for the NFS server daemon. Falls back to
# nfs-utils (older naming) if the primary name isn't found, and is a no-op
# if the server is already installed (a stock SLES 16 image doesn't ship it).
if ! systemctl list-unit-files nfs-server.service &>/dev/null; then
  log "installing NFS server package ..."
  if ! zypper --non-interactive install nfs-kernel-server 2>/dev/null; then
    zypper --non-interactive install nfs-utils \
      || { echo ">>> [nfs-backup] FAILED to install an NFS server package" >&2; exit 1; }
  fi
fi

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

systemctl enable --now nfs-server \
  || { echo ">>> [nfs-backup] FAILED to start nfs-server" >&2; exit 1; }

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
