"""Hauler store population — airgap artifacts for EIB image builds."""
from __future__ import annotations

from typing import Generator

from ..runner import DeployEvent, LogLine


class HaulerMixin:
    """Populate the Hauler OCI registry + fileserver on the eib VM."""

    def _populate_hauler(self) -> Generator[DeployEvent, None, bool]:
        """Populate the Hauler store on the eib VM with SUSE Edge artifacts.

        Runs after all Rancher/Elemental artifacts are fully downloaded so internet
        bandwidth is free. Downloads into /var/lib/hauler on the eib VM, then
        enables and starts the Hauler OCI registry (port 5000) and fileserver
        (port 8080) so participants can build EIB images fully offline.

        Also pre-stages the EIB image definition template at /home/eib-config/ with
        a placeholder for the MachineRegistration URL that participants fill in.
        """
        prefix = self.elemental_reg_prefix
        reg_name = f"{prefix}-reg-1"
        # Fixed lowercase names, not derived from the upstream URL: hauler's `store
        # add file` reference-name parser rejects uppercase (confirmed live —
        # "could not parse reference" with no name given; works once --name is
        # lowercase), and openSUSE's filenames ("openSUSE-Leap-Micro...") are
        # uppercase. Deterministic names also decouple us from upstream renames.
        iso_fname = "leap-micro-selfinstall.iso"
        raw_fname_dl = "leap-micro-default.raw.xz"
        # openSUSE ships the raw appliance .xz-compressed; EIB needs a plain .raw
        # baseImage, so it gets decompressed after staging (see the curl/xz block
        # below). raw_fname is the name EIB definitions actually reference.
        raw_fname = "leap-micro-default.raw"
        raw_decompress_cmd = f'xz -d -f "/home/eib-config/base-images/{raw_fname_dl}"\n'

        script = (
            "set -euo pipefail\n"
            "STORE=/var/lib/hauler\n"
            "HAULER=/usr/local/bin/hauler\n\n"
            # Mirror the EIB container image into Hauler so participants can run
            # EIB without internet access from the eib VM.
            f'$HAULER store add image "{self.eib_image}" --store $STORE\n'
            # Elemental register agent — EIB embeds this into the edge node image
            # so nodes can phone home to the Elemental Operator on first boot.
            # There is no standalone "elemental-register" image at registry.suse.com
            # (confirmed live: NAME_UNKNOWN) — the register binary ships inside the
            # elemental-operator image itself, same tag as the operator Deployment
            # (confirmed live: registry.suse.com/rancher/elemental-operator:1.9.0
            # pulls fine; this is the exact image already deployed by the elemental
            # phase's own Helm install a few steps earlier).
            f'$HAULER store add image "registry.suse.com/rancher/elemental-operator:{self.elemental_op_version}" --store $STORE\n'
            # Demo app image (Fleet-deployed to edge clusters, from cfg["alien_geeko"]["image"]);
            # edge nodes pull from Hauler via k3s registry mirror (docker.io → eib:5000).
            f'$HAULER store add image "{self.alien_geeko_image}" --store $STORE\n'
            # Leap Micro 6.2 SelfInstall ISO — EIB base for Elemental ISO builds (edge1/edge2).
            # Download via curl, not hauler's own HTTP client: opensuse.org's
            # redirector picks a rotating mirror, and at least one observed mirror
            # (pkg.adfinis-on-exoscale.ch) fails hauler's Go TLS client outright
            # ("tls: protocol version not supported") — curl (used everywhere else
            # in this codebase for large downloads) negotiates it fine. Then add the
            # already-downloaded local file with an explicit lowercase --name.
            f'curl -4 --http1.1 -fsSL --retry 5 --retry-delay 10 --retry-all-errors '
            f'-o "/tmp/{iso_fname}" "{self.leap_micro_iso_url}"\n'
            f'$HAULER store add file "/tmp/{iso_fname}" --name "{iso_fname}" --store $STORE\n'
            f'rm -f "/tmp/{iso_fname}"\n'
            # Leap Micro 6.2 Default RAW (.xz) — EIB base for standalone K3s/RKE2 builds (edge3/edge4)
            f'curl -4 --http1.1 -fsSL --retry 5 --retry-delay 10 --retry-all-errors '
            f'-o "/tmp/{raw_fname_dl}" "{self.leap_micro_raw_url}"\n'
            f'$HAULER store add file "/tmp/{raw_fname_dl}" --name "{raw_fname_dl}" --store $STORE\n'
            f'rm -f "/tmp/{raw_fname_dl}"\n\n'
            # Enable and start Hauler services (service units written by cloud-init)
            "systemctl daemon-reload\n"
            "systemctl enable --now hauler-registry.service hauler-fileserver.service\n\n"
            # enable --now returns once systemd has forked the unit, not once the
            # fileserver is actually bound and listening — curling immediately here
            # raced the startup and failed "Could not connect to server". Poll until
            # it answers (fileserver has no dedicated health path; a bare GET 404
            # still proves the socket is up) before staging the base images below.
            "for i in $(seq 1 30); do\n"
            '  curl -sS -o /dev/null "http://localhost:8080/" 2>/dev/null && break\n'
            "  sleep 1\n"
            "done\n\n"
            # Stage Leap Micro base images from Hauler fileserver into eib-config/base-images
            # so participants can reference them by filename in EIB definition files without
            # needing internet. The ISO is for Elemental builds; the RAW is for standalone builds.
            "mkdir -p /home/eib-config/scripts /home/eib-config/base-images /home/eib-output\n"
            f'curl -fsSL "http://localhost:8080/{iso_fname}" -o "/home/eib-config/base-images/{iso_fname}"\n'
            f'curl -fsSL "http://localhost:8080/{raw_fname_dl}" -o "/home/eib-config/base-images/{raw_fname_dl}"\n'
            f"{raw_decompress_cmd}\n"
            # k3s registry mirror script — EIB runs this during image build to embed
            # /etc/rancher/k3s/registries.yaml into the edge node OS so ALL container
            # pulls (docker.io, registry.suse.com, ghcr.io) go through the Hauler
            # registry at boot time, keeping edge nodes fully airgapped.
            f"cat > /home/eib-config/scripts/99-k3s-registries.sh << 'K3S_REG'\n"
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "mkdir -p /etc/rancher/k3s\n"
            "cat > /etc/rancher/k3s/registries.yaml << 'EOF'\n"
            "mirrors:\n"
            '  "docker.io":\n'
            "    endpoint:\n"
            f'      - "http://{self.eib_ip}:5000"\n'
            '  "registry.suse.com":\n'
            "    endpoint:\n"
            f'      - "http://{self.eib_ip}:5000"\n'
            '  "ghcr.io":\n'
            "    endpoint:\n"
            f'      - "http://{self.eib_ip}:5000"\n'
            "EOF\n"
            "K3S_REG\n"
            "chmod +x /home/eib-config/scripts/99-k3s-registries.sh\n\n"
            # Pre-stage EIB definition template for participants.
            # EIB 1.3.3 does NOT have a top-level elemental: key — Elemental registration
            # is configured via embeddedArtifacts (checked at build time from the Hauler store).
            f"cat > /home/eib-config/edge-definition.yaml << '__EIB_DEF__'\n"
            "apiVersion: 1.0\n\n"
            "image:\n"
            "  imageType: raw\n"
            "  arch: x86_64\n"
            f"  baseImage: {raw_fname}\n"
            "  outputImageName: elemental-edge.raw\n\n"
            "operatingSystem:\n"
            "  kernelArgs:\n"
            "    - net.ifnames=0\n"
            "  scripts:\n"
            "    - 99-k3s-registries.sh\n\n"
            "embeddedArtifacts:\n"
            "  registries:\n"
            "    urls:\n"
            f"      - {self.eib_ip}:5000\n"
            "__EIB_DEF__\n"
        )
        yield LogLine(
            f"Populating Hauler store on eib VM ({self.eib_ip}) "
            "with SUSE Edge artifacts (may take 15-30 min)..."
        )
        r = self._eib_ssh_script(script, timeout=2400)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Hauler store population failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine(
            "  Hauler store populated. Registry: "
            f"http://{self.eib_ip}:5000  Fileserver: http://{self.eib_ip}:8080"
        )
        yield LogLine(
            f"  EIB definition template: /home/eib-config/edge-definition.yaml\n"
            f"  Set registration URL: kubectl get machineregistration {reg_name} "
            f"-n fleet-default -o jsonpath='{{{{.status.registrationURL}}}}'"
        )
        return True
