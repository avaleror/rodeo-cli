#!/bin/bash
# virt-rodeo custom_scripts step: the SLES images the suse-virt-rodeo track
# expects. Only in the virt-rodeo profile.
#
# The Instruqt host image has two SLES cloud images built in:
#   - SLES15-SP7-Minimal-VM.x86_64-Cloud-GM.qcow2, served from the host cache
#     (http://192.168.122.1:8889/), which chapters 3+ import;
#   - SLES-16.0-Minimal-VM.x86_64-Cloud-GM.qcow2, already a Harvester image in
#     namespace official-images (displayName = file name), which chapter 3's
#     the-engine-01 boots from.
# SLES images need a SUSE Customer Center login, so they cannot be downloaded
# anonymously like the Leap image in 50-image-cache.sh. They live in a private
# bucket; the operator's laptop writes short-lived download links to
# LIST_FILE (`rodeo fleet lab-images`), one image per line:
#     <file name> <https url> [harvester]
# "harvester" also registers it as a Harvester image in official-images.
#
# No list file: nothing to do, the deploy carries on (chapters 1-2 and 4-7
# do not need SLES). Idempotent: files already cached, and Harvester images
# that already exist, are left alone.

set -uo pipefail
export KUBECONFIG="${KUBECONFIG:-/root/.rodeo/harvester-kubeconfig}"

log(){ echo ">>> [sles-images] $*"; }

LIST_FILE="${RODEO_LAB_IMAGES_LIST:-/etc/rodeo/lab-images.list}"
IMAGE_DIR="${RODEO_IMAGE_CACHE_DIR:-/var/lib/libvirt/images/workshop-cache}"
CACHE_URL="http://192.168.122.1:8889"
IMAGE_NS="official-images"
FILE_RE='^[A-Za-z0-9][A-Za-z0-9._-]*\.qcow2$'
# The Harvester nodes (definition.yaml: harvester1-3), the only cache clients.
CACHE_CLIENTS="${RODEO_IMAGE_CACHE_CLIENTS:-192.168.122.11-192.168.122.13}"

if [ ! -s "${LIST_FILE}" ]; then
  log "no ${LIST_FILE}: skipping (run 'rodeo fleet lab-images' to add the SLES images chapter 3 needs)"
  exit 0
fi
mkdir -p "${IMAGE_DIR}"

is_qcow2(){ [ "$(head -c 4 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n')" = "514649fb" ]; }

FAILED=0
HARVESTER=()
while read -r file url flag; do
  [ -z "${file:-}" ] && continue
  case "${file}" in \#*) continue ;; esac
  if ! [[ "${file}" =~ ${FILE_RE} ]] || [[ "${url:-}" != https://* ]]; then
    log "skipping a malformed line for '${file}'"
    FAILED=1
    continue
  fi
  dest="${IMAGE_DIR}/${file}"
  if is_qcow2 "${dest}"; then
    log "${file} already cached"
  else
    log "downloading ${file} ..."
    # The URL is a signed link: never echo it, it is a credential until it expires.
    if curl -fsSL --retry 3 --max-time 1800 -o "${dest}.part" "${url}" && is_qcow2 "${dest}.part"; then
      mv -f "${dest}.part" "${dest}"
      # Root only: the cache server runs as root; students on the host must
      # not copy licensed images out of the lab.
      chmod 600 "${dest}"
      log "${file} cached ($(du -h "${dest}" | cut -f1))"
    else
      rm -f "${dest}.part"
      log "FAILED to download ${file} (expired link, or not a qcow2 image)"
      FAILED=1
      continue
    fi
  fi
  chmod 600 "${dest}"   # also for copies cached by an older run
  [ "${flag:-}" = "harvester" ] && HARVESTER+=("${file}")
done < "${LIST_FILE}"

# Only the Harvester nodes may download from the cache: they import the
# images (chapter 3, and the student's own import in chapter 4). Anyone else
# on the lab network (a VM the student creates, or the student's shell on
# this host, whose traffic to 192.168.122.1 arrives here too) is dropped, so
# a licensed image cannot be pulled out of the lab through the cache. Its
# own nftables table: firewalld and libvirt never touch it, and a drop in
# any input chain wins over accepts elsewhere.
GUARD_FILE="${RODEO_IMAGE_CACHE_GUARD:-/etc/rodeo/image-cache-guard.nft}"
UNIT_DIR="${RODEO_SYSTEMD_DIR:-/etc/systemd/system}"
mkdir -p "$(dirname "${GUARD_FILE}")"
cat > "${GUARD_FILE}.tmp" <<EOF
table inet rodeo_image_cache
delete table inet rodeo_image_cache
table inet rodeo_image_cache {
  chain input {
    type filter hook input priority filter - 5; policy accept;
    ip daddr 192.168.122.1 tcp dport 8889 ip saddr != { ${CACHE_CLIENTS} } drop
  }
}
EOF
if nft -c -f "${GUARD_FILE}.tmp" && nft -f "${GUARD_FILE}.tmp"; then
  mv -f "${GUARD_FILE}.tmp" "${GUARD_FILE}"
  cat > "${UNIT_DIR}/rodeo-image-cache-guard.service" <<EOF
[Unit]
Description=rodeo virt-rodeo: image cache only for the Harvester nodes
After=network-pre.target
Before=rodeo-image-cache.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f ${GUARD_FILE}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload && systemctl enable -q rodeo-image-cache-guard
  log "image cache limited to ${CACHE_CLIENTS}"
else
  rm -f "${GUARD_FILE}.tmp"
  log "FAILED to limit the image cache to the Harvester nodes"
  FAILED=1
fi

if [ "${#HARVESTER[@]}" -gt 0 ]; then
  kubectl create namespace "${IMAGE_NS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
fi
for file in "${HARVESTER[@]}"; do
  existing="$(kubectl get virtualmachineimages.harvesterhci.io -n "${IMAGE_NS}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.displayName}{"\n"}{end}' 2>/dev/null \
    | awk -v d="${file}" '$2 == d {print $1}' | head -n1)"
  if [ -z "${existing}" ]; then
    log "registering ${file} as a Harvester image in ${IMAGE_NS} ..."
    cat <<EOF | kubectl create -f - >/dev/null || { log "FAILED to create the Harvester image ${file}"; FAILED=1; continue; }
apiVersion: harvesterhci.io/v1beta1
kind: VirtualMachineImage
metadata:
  generateName: image-
  namespace: ${IMAGE_NS}
spec:
  sourceType: download
  displayName: ${file}
  url: ${CACHE_URL}/${file}
  storageClassParameters:
    numberOfReplicas: "1"
EOF
  else
    log "${file} already registered in ${IMAGE_NS} (${existing})"
  fi
  progress=""
  for _ in $(seq 1 60); do
    progress="$(kubectl get virtualmachineimages.harvesterhci.io -n "${IMAGE_NS}" \
      -o jsonpath='{range .items[*]}{.spec.displayName}{" "}{.status.progress}{"\n"}{end}' 2>/dev/null \
      | awk -v d="${file}" '$1 == d {print $2}' | head -n1)"
    [ "${progress}" = "100" ] && break
    sleep 10
  done
  if [ "${progress}" = "100" ]; then
    log "${file} is Active in ${IMAGE_NS}"
  else
    log "FAILED: ${file} not Active after 10 minutes (progress=${progress:-none})"
    FAILED=1
  fi
done

if [ "${FAILED}" -eq 0 ]; then
  # The signed links are credentials until they expire: once every image is
  # cached and registered, nothing needs them on this host any more.
  rm -f "${LIST_FILE}"
  log "custom_scripts step complete (link list removed)."
else
  log "finished with failures (see above); link list kept for a retry"
fi
exit "${FAILED}"
