"""Elemental operator install and MachineRegistration creation."""
from __future__ import annotations

from typing import Generator

from ..runner import DeployEvent, LogLine


class ElementalMixin:
    """Elemental OS management (suse-edge)."""

    def _install_elemental(self) -> Generator[DeployEvent, None, bool]:
        """Install Elemental Operator CRDs + Operator, UI extension, and MachineRegistrations."""
        namespace = "cattle-elemental-system"
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"helm upgrade --install elemental-operator-crds"
            f" oci://registry.suse.com/rancher/elemental-operator-crds-chart"
            f" --version {self.elemental_crds_version}"
            f" --namespace {namespace} --create-namespace"
            f" --wait --timeout 3m\n"
            f"helm upgrade --install elemental-operator"
            f" oci://registry.suse.com/rancher/elemental-operator-chart"
            f" --version {self.elemental_op_version}"
            f" --namespace {namespace}"
            f" --wait --timeout 5m\n"
        )
        yield LogLine(
            f"Installing Elemental Operator {self.elemental_op_version} "
            "(CRDs + Operator, up to 8 min)..."
        )
        r = self._ssh_script(script, timeout=540)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "Elemental Operator install failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine("  Elemental Operator installed.")

        # UI extension, repos, and MachineRegistrations are suse-edge-specific.
        # Other profiles (e.g. future rancher-only) may use the Operator without the UI.
        if self.profile_type == "suse-edge":
            if not (yield from self._add_extension_repos()):
                return False
            if not (yield from self._create_machine_registrations()):
                return False
            if not (yield from self._populate_hauler()):
                return False
            if not (yield from self._deploy_gitea()):
                return False
            if not (yield from self._create_alien_geeko_fleet()):
                return False
        return True
    def _create_machine_registrations(self) -> Generator[DeployEvent, None, bool]:
        """Create Elemental MachineRegistration CRs in fleet-default.

        Creates self.elemental_reg_count registrations named
        {prefix}-reg-1, {prefix}-reg-2, ... Each gets a distinct label
        so students can target specific registrations in their EIB image config.
        """
        if self.elemental_reg_count < 1:
            return True

        prefix = self.elemental_reg_prefix
        yield LogLine(
            f"Creating {self.elemental_reg_count} MachineRegistration(s) "
            f"({prefix}-reg-1 .. {prefix}-reg-{self.elemental_reg_count})..."
        )

        manifests = []
        for n in range(1, self.elemental_reg_count + 1):
            name = f"{prefix}-reg-{n}"
            manifests.append(
                f"apiVersion: elemental.cattle.io/v1beta1\n"
                f"kind: MachineRegistration\n"
                f"metadata:\n"
                f"  name: {name}\n"
                f"  namespace: fleet-default\n"
                f"spec:\n"
                # machineName uses SMBIOS fields — interpreted by Elemental at boot time,
                # not by the shell. The literal ${} must reach the cluster as-is.
                f"  machineName: '${{System Information/Manufacturer}}-${{System Information/UUID}}'\n"
                f"  machineInventoryLabels:\n"
                f"    manufacturer: '${{System Information/Manufacturer}}'\n"
                f"    productName: '${{System Information/Product Name}}'\n"
                f"    registration: '{name}'\n"
            )

        combined = "---\n" + "\n---\n".join(manifests)
        script = (
            "set -euo pipefail\n"
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            f"cat <<'__ELEMENTAL_MANIFEST__' | kubectl apply -f -\n"
            f"{combined}\n"
            "__ELEMENTAL_MANIFEST__\n"
        )
        r = self._ssh_script(script, timeout=60)
        for line in (r.stdout + r.stderr).splitlines():
            if line.strip():
                yield LogLine(f"  {line}")
        if r.returncode != 0:
            self.error = "MachineRegistration creation failed"
            yield LogLine(f"  ✗ {self.error}")
            return False
        yield LogLine(
            f"  MachineRegistration(s) created. "
            f"Retrieve URL: kubectl get machineregistration {prefix}-reg-1 "
            f"-n fleet-default -o jsonpath='{{.status.registrationURL}}'"
        )
        return True
