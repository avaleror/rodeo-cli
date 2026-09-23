#!/bin/bash
# suse-virt-workshop custom_scripts step 1/3.
#
# Downloads and caches a small, freely redistributable cloud image, then
# serves it over HTTP on the libvirt NAT gateway (192.168.122.1) so
# harvester1-3 can import it as a VirtualMachineImage without any external
# network dependency at exercise time — the same role Exercise 2.7's manual
# image upload plays, done once at deploy time instead of by hand.
#
# WHY THIS IMAGE: suse-virt-rodeo's own equivalent (SLES15-SP7-Minimal-VM) is
# gated behind SUSE Customer Center — dl.suse.com URLs are signed and expire,
# so there is no way to auto-download it on a self-hosted deploy with no SCC
# registration. Leap-16.0-Minimal-VM.x86_64-Cloud.qcow2 is the freely
# downloadable, no-registration equivalent: same openSUSE distribution host,
# confirmed ~1.42 GiB virtual size (read from the qcow2 header directly —
# small enough for a plain 5Gi root-disk PVC, matching suse-virt-rodeo's own
# SLES15-SP7-Minimal-VM/SLES-16.0-Minimal-VM sizing), and its own build SBOM
# (Leap-16.0-Minimal-VM.x86_64-Cloud.json) lists both `cloud-init` and
# `cloud-init-config-suse` as installed packages.
#
# NOT Leap-16.0-Minimal-VM.x86_64-kvm-and-xen.qcow2 (used here until
# 2026-09-23) — same appliance family, different build variant: its SBOM
# ships combustion only, not cloud-init, so the NoCloud ISO Harvester/KubeVirt
# attaches for Exercise 2.8's user-data and 70-webserver-prod.sh's own
# cloud-init both get silently ignored — the VM boots and qemu-guest-agent
# connects fine, but no SSH key ever lands anywhere, in any user account.
#
# ALSO NOT openSUSE-Leap-Micro.x86_64-Default-qcow.qcow2 (used here
# 2026-09-18 through 2026-09-23, after the kvm-and-xen mistake above) — that
# one does ship cloud-init, but Leap Micro's btrfs layout reports a fixed
# ~32 GiB virtualSize regardless of its ~1.5 GiB download (confirmed via
# `virt-resize --shrink`: not a sparse/shrinkable image, per suse-virt-rodeo's
# own track_scripts/setup-kvm-host, which hit the identical
# FailedAttachVolume/"insufficient storage" failure on a 5Gi PVC before
# switching away from it). Every VM built from it needs a 32-33 GiB root
# disk instead of 5 GiB — a 6-7x jump that isn't reflected anywhere in this
# workshop's or rodeo-cli's disk_gb sizing, and can exhaust a 3-node lab's
# Longhorn capacity well before all 8 exercises' VMs are built.
#
# Idempotent: skips the download if the file is already present and its
# checksum matches — safe to re-run on every `rodeo up` (this is a
# no_cache_phase). The checksum itself is fetched fresh each run rather than
# hardcoded, so a future openSUSE rebuild under the same filename doesn't
# require updating this script.

set -uo pipefail

log(){ echo ">>> [image-cache] $*"; }

IMAGE_DIR="/var/lib/libvirt/images/workshop-cache"
IMAGE_FILE="Leap-16.0-Minimal-VM.x86_64-Cloud.qcow2"
IMAGE_URL="https://download.opensuse.org/distribution/leap/16.0/appliances/${IMAGE_FILE}"
IMAGE_HTTP_PORT=8889
IMAGE_HTTP_BIND="192.168.122.1"

mkdir -p "${IMAGE_DIR}"

need_download=1
if [ -s "${IMAGE_DIR}/${IMAGE_FILE}" ]; then
  log "checking existing ${IMAGE_FILE} against upstream checksum ..."
  REMOTE_SUM="$(curl -fsSL --max-time 20 "${IMAGE_URL}.sha256" 2>/dev/null | awk '{print $1}')"
  LOCAL_SUM="$(sha256sum "${IMAGE_DIR}/${IMAGE_FILE}" 2>/dev/null | awk '{print $1}')"
  if [ -n "${REMOTE_SUM}" ] && [ "${REMOTE_SUM}" = "${LOCAL_SUM}" ]; then
    log "already cached and checksum matches, skipping download."
    need_download=0
  else
    log "missing/stale, re-downloading."
  fi
fi

if [ "${need_download}" = "1" ]; then
  log "downloading ${IMAGE_FILE} (~322 MiB) ..."
  if ! curl -fsSL --max-time 300 -o "${IMAGE_DIR}/${IMAGE_FILE}.part" "${IMAGE_URL}"; then
    echo ">>> [image-cache] FAILED to download ${IMAGE_URL}" >&2
    exit 1
  fi
  REMOTE_SUM="$(curl -fsSL --max-time 20 "${IMAGE_URL}.sha256" 2>/dev/null | awk '{print $1}')"
  LOCAL_SUM="$(sha256sum "${IMAGE_DIR}/${IMAGE_FILE}.part" | awk '{print $1}')"
  if [ -n "${REMOTE_SUM}" ] && [ "${REMOTE_SUM}" != "${LOCAL_SUM}" ]; then
    echo ">>> [image-cache] FAILED checksum mismatch (remote=${REMOTE_SUM} local=${LOCAL_SUM})" >&2
    rm -f "${IMAGE_DIR}/${IMAGE_FILE}.part"
    exit 1
  fi
  mv "${IMAGE_DIR}/${IMAGE_FILE}.part" "${IMAGE_DIR}/${IMAGE_FILE}"
  log "download verified."
fi

# Serve via a small systemd unit rather than a bare background process, so
# the cache survives a host reboot the same way finalise's libvirt-guests
# autostart does for the VMs themselves.
UNIT=/etc/systemd/system/rodeo-image-cache.service
if [ ! -f "${UNIT}" ] || ! grep -q "${IMAGE_DIR}" "${UNIT}" 2>/dev/null; then
  log "installing rodeo-image-cache.service ..."
  cat > "${UNIT}" <<EOF
[Unit]
Description=rodeo suse-virt-workshop image cache (HTTP)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${IMAGE_DIR}
ExecStart=/usr/bin/python3 -m http.server ${IMAGE_HTTP_PORT} --bind ${IMAGE_HTTP_BIND} --directory ${IMAGE_DIR}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
fi

systemctl enable --now rodeo-image-cache.service \
  || { echo ">>> [image-cache] FAILED to start rodeo-image-cache.service" >&2; exit 1; }

log "serving ${IMAGE_FILE} at http://${IMAGE_HTTP_BIND}:${IMAGE_HTTP_PORT}/${IMAGE_FILE}"
exit 0
