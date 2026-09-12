#!/bin/bash
# virt-workshop-aws custom_scripts step 1/3.
#
# Downloads and caches a small, freely redistributable cloud image, then
# serves it over HTTP on the libvirt NAT gateway (192.168.122.1) so
# harvester1-3 can import it as a VirtualMachineImage without any external
# network dependency at exercise time.
#
# WHY THIS IMAGE: suse-virt-rodeo's own equivalent (SLES15-SP7-Minimal-VM)
# is gated behind SUSE Customer Center — dl.suse.com URLs are signed and
# expire, so there is no way to auto-download it for a public/AWS profile
# (see that repo's track_scripts/setup-kvm-host for the full story). openSUSE
# Leap 16.0's KVM appliance is the closest freely-downloadable equivalent:
# same small-cloud-image shape (~308 MiB download), no registration, stable
# distribution URL. Verified live 2026-09-12 (HTTP 200, real content-length)
# before writing this script — see docs/reference/virt-workshop-aws.md.
#
# Idempotent: skips the download if the file is already present and its
# checksum matches: safe to re-run on every `rodeo up` (this is a
# no_cache_phase). The checksum itself is fetched fresh each run rather than
# hardcoded, so a future openSUSE rebuild under the same filename doesn't
# require updating this script.

set -uo pipefail

log(){ echo ">>> [image-cache] $*"; }

IMAGE_DIR="/var/lib/libvirt/images/workshop-cache"
IMAGE_FILE="Leap-16.0-Minimal-VM.x86_64-kvm-and-xen.qcow2"
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
  log "downloading ${IMAGE_FILE} (~308 MiB) ..."
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
Description=rodeo virt-workshop-aws image cache (HTTP)
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
