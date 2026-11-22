"""Structured host readiness report for ``rodeo doctor --output json``."""
from __future__ import annotations

import os
from pathlib import Path
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
        "agent_kit": agent_kit_report(),
    }


def agent_kit_report(lab: Path | None = None) -> dict[str, Any]:
    """Whether ``rodeo agent-setup``'s Claude Code kit is in the lab (or the cwd).

    It can tell that the kit is installed, not that an agent session uses it;
    ``in_claude_code`` only says this doctor run came from a Claude Code shell.
    """
    from ..agent_kit import status
    from ..config import find_lab_dir

    lab = lab or find_lab_dir(use_last_lab=False) or Path.cwd()
    report = status(lab)
    report["in_claude_code"] = os.environ.get("CLAUDECODE") == "1"
    return report
