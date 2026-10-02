#!/bin/bash
# export-image.sh — run ON THE HOST after generalise.sh powered the smlm VM off.
#
# Writes a compressed copy of the VM disk plus its sha256 to OUT_DIR (default:
# current directory). Store the .qcow2 in a private location — downloadable by
# anyone in the account that stores it, never public — then give the
# workshop plan's operator secrets: smlm_image_url, smlm_image_sha256 (printed
# below) and smlm_image_admin_pass (from this host's ~/.rodeo/secrets.yaml).
set -euo pipefail

vm="smlm.rodeo.lab"
disk="/var/lib/libvirt/images/${vm}.qcow2"
out_dir="${OUT_DIR:-.}"
image="${out_dir}/smlm-workshop-server.qcow2"

state=$(virsh domstate "${vm}" 2>/dev/null || echo missing)
if [[ "${state}" != "shut off" ]]; then
    echo "ERROR: ${vm} is '${state}' — run generalise.sh inside it first." >&2
    exit 1
fi

echo "# Compressing ${disk} -> ${image}"
qemu-img convert -p -c -O qcow2 "${disk}" "${image}"
(cd "${out_dir}" && sha256sum "$(basename "${image}")" > "$(basename "${image}").sha256")
echo "# sha256 (smlm_image_sha256):"
cut -d' ' -f1 "${image}.sha256"
