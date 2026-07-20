"""Structured host readiness report for ``rodeo doctor --output json``."""
from __future__ import annotations

from typing import Any

from ..preflight import detect_host, recommend_profile


def doctor_report(image_dir: str | None = None) -> dict[str, Any]:
    """Return a JSON-serializable doctor report for the local host."""
    host = detect_host() if image_dir is None else detect_host(image_dir)
    recommended, fits = recommend_profile(host)
    return {
        "host": {
            "ram_total_gib": host.get("ram_total_gib", 0),
            "ram_avail_gib": host.get("ram_avail_gib", 0),
            "cpus": host.get("cpus", 0),
            "disk_free_gib": host.get("disk_free_gib", -1),
            "image_dir": host.get("image_dir", ""),
            "pkg_mgr": host.get("pkg_mgr", "unknown"),
            "has_kvm": bool(host.get("has_kvm")),
            "nested": bool(host.get("nested")),
            "is_root": bool(host.get("is_root")),
        },
        "core_tools": dict(host.get("core_tools") or {}),
        "py_modules": dict(host.get("py_modules") or {}),
        "optional_tools": dict(host.get("optional_tools") or {}),
        "recommended_profile": recommended,
        "profile_fits": fits,
    }
