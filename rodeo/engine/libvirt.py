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
        vms = []
        for name in names:
            try:
                dom = self.conn.lookupByName(name)
                state_id, _ = dom.state()
                vms.append(VMInfo(
                    name=name,
                    state=_STATE_MAP.get(state_id, "unknown"),
                    domain_id=dom.ID() if dom.ID() != -1 else None,
                    autostart=bool(dom.autostart()),
                ))
            except _libvirt.libvirtError:
                vms.append(VMInfo(name=name, state="not found"))
        return vms

    def get_vm(self, name: str) -> VMInfo:
        return self.list_vms(names=[name])[0]

    def start(self, name: str) -> None:
        dom = self.conn.lookupByName(name)
        if dom.state()[0] != 1:  # VIR_DOMAIN_RUNNING
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
            return dom.state()[0] == 1  # VIR_DOMAIN_RUNNING
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
