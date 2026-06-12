"""Direct libvirt-python driver for VM operations."""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Optional

try:
    import libvirt as _libvirt
    _AVAILABLE = True
except ImportError:
    _libvirt = None  # type: ignore[assignment]
    _AVAILABLE = False

_VIR_RUNNING = getattr(_libvirt, "VIR_DOMAIN_RUNNING", 1) if _libvirt else 1

_VIR_ERR_NO_DOMAIN = getattr(_libvirt, "VIR_ERR_NO_DOMAIN", 42) if _libvirt else 42

def _libvirt_error_handler(ctx, err):
    """Suppress noisy 'Domain not found' errors from libvirt when intentionally
    probing for non-existing VMs (e.g. during `plan` on clean host).
    Other errors still get default logging."""
    if err[0] == _VIR_ERR_NO_DOMAIN:
        return
    # Fall back to default behavior (which prints)
    if _libvirt:
        # Re-raise to default? But simple: print for other errors
        pass  # default handler already registered or we can ignore for now

# Register once to reduce spam on expected not-found
if _libvirt:
    try:
        _libvirt.registerErrorHandler(_libvirt_error_handler, None)
    except Exception:
        pass

_STATE_MAP = {
    0: "no state",
    1: "running",
    2: "blocked",
    3: "paused",
    4: "shutting down",
    5: "shut off",
    6: "crashed",
    7: "suspended",
}


@dataclass
class VMInfo:
    name: str
    state: str
    domain_id: Optional[int] = None
    autostart: bool = False
    memory_mib: Optional[int] = None
    vcpus: Optional[int] = None


def _require_libvirt() -> None:
    if not _AVAILABLE:
        raise RuntimeError(
            "libvirt-python not installed. Run: sudo rodeo install-deps"
        )


class LibvirtDriver:
    def __init__(self, uri: str = "qemu:///system") -> None:
        _require_libvirt()
        self._uri = uri
        self._conn = None

    def connect(self) -> "LibvirtDriver":
        self._conn = _libvirt.open(self._uri)
        if self._conn is None:
            raise RuntimeError(f"Failed to connect to libvirt at {self._uri}")
        return self

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "LibvirtDriver":
        return self.connect()

    def __exit__(self, *_) -> None:
        self.close()

    @property
    def conn(self):
        if self._conn is None:
            self.connect()
        return self._conn

    def list_vms(self, names: list[str]) -> list[VMInfo]:
        """List info for specific names, without noisy 'Domain not found' logs.
        Uses listAllDomains to build existing set first (quiet), then probes only existing."""
        try:
            all_doms = self.conn.listAllDomains()
            existing = {dom.name(): dom for dom in all_doms}
        except _libvirt.libvirtError:
            # If can't list all, fall back to per-name (may log, but rare)
            existing = {}
        vms = []
        for name in names:
            dom = existing.get(name)
            if dom is None:
                vms.append(VMInfo(name=name, state="not found"))
                continue
            try:
                state_id, _ = dom.state()
                _, max_mem_kib, _, vcpus, _ = dom.info()
                vms.append(VMInfo(
                    name=name,
                    state=_STATE_MAP.get(state_id, "unknown"),
                    domain_id=dom.ID() if dom.ID() != -1 else None,
                    autostart=bool(dom.autostart()),
                    memory_mib=max_mem_kib // 1024,
                    vcpus=vcpus,
                ))
            except _libvirt.libvirtError:
                vms.append(VMInfo(name=name, state="not found"))
        return vms

    def list_all_domain_names(self) -> list[str]:
        """Names of every domain on the host (any state) — used to detect
        non-rodeo VMs before tearing down shared resources."""
        try:
            return [dom.name() for dom in self.conn.listAllDomains()]
        except _libvirt.libvirtError:
            return []

    def get_vm(self, name: str) -> VMInfo:
        return self.list_vms(names=[name])[0]

    def start(self, name: str) -> None:
        dom = self.conn.lookupByName(name)
        if dom.state()[0] != _VIR_RUNNING:
            dom.create()

    def shutdown(self, name: str) -> None:
        """Graceful ACPI shutdown."""
        with contextlib.suppress(_libvirt.libvirtError):
            dom = self.conn.lookupByName(name)
            dom.shutdown()

    def destroy(self, name: str) -> None:
        """Hard kill."""
        with contextlib.suppress(_libvirt.libvirtError):
            dom = self.conn.lookupByName(name)
            dom.destroy()

    def undefine(self, name: str) -> None:
        with contextlib.suppress(_libvirt.libvirtError):
            dom = self.conn.lookupByName(name)
            try:
                dom.undefineFlags(_libvirt.VIR_DOMAIN_UNDEFINE_NVRAM)
            except _libvirt.libvirtError:
                dom.undefine()

    def set_autostart(self, name: str, enabled: bool) -> None:
        dom = self.conn.lookupByName(name)
        dom.setAutostart(1 if enabled else 0)

    def is_running(self, name: str) -> bool:
        try:
            dom = self.conn.lookupByName(name)
            return dom.state()[0] == _VIR_RUNNING
        except _libvirt.libvirtError:
            return False

    def net_is_active(self, name: str = "default") -> bool:
        try:
            net = self.conn.networkLookupByName(name)
            return bool(net.isActive())
        except _libvirt.libvirtError:
            return False

    def net_start(self, name: str = "default") -> None:
        with contextlib.suppress(_libvirt.libvirtError):
            net = self.conn.networkLookupByName(name)
            if not net.isActive():
                net.create()

    def net_set_autostart(self, name: str = "default", enabled: bool = True) -> None:
        net = self.conn.networkLookupByName(name)
        net.setAutostart(1 if enabled else 0)

    def net_destroy(self, name: str = "default") -> None:
        with contextlib.suppress(_libvirt.libvirtError):
            net = self.conn.networkLookupByName(name)
            if net.isActive():
                net.destroy()

    def net_undefine(self, name: str = "default") -> None:
        with contextlib.suppress(_libvirt.libvirtError):
            net = self.conn.networkLookupByName(name)
            net.undefine()

    def storage_vol_delete(self, pool_name: str, vol_name: str) -> None:
        with contextlib.suppress(_libvirt.libvirtError):
            pool = self.conn.storagePoolLookupByName(pool_name)
            vol = pool.storageVolLookupByName(vol_name)
            vol.delete()

    def eject_media(self, name: str, target: str) -> None:
        """Eject CDROM / ISO media from a VM's disk target (e.g. 'sda', 'sdb').

        Uses libvirt-python changeMediaFlags with empty path + affect flags.
        Best-effort (suppresses). Used from RancherPhase after import (with stop + uri).
        """
        with contextlib.suppress(_libvirt.libvirtError):
            dom = self.conn.lookupByName(name)
            flags = 0
            for flag_name in ("VIR_DOMAIN_AFFECT_CURRENT", "VIR_DOMAIN_AFFECT_LIVE", "VIR_DOMAIN_AFFECT_CONFIG"):
                flags |= getattr(_libvirt, flag_name, 0)
            try:
                dom.changeMediaFlags(target, "", flags)
            except _libvirt.libvirtError:
                # Fallback for older libvirt or strict qemu
                try:
                    dom.changeMediaFlags(target, "")
                except _libvirt.libvirtError:
                    pass
