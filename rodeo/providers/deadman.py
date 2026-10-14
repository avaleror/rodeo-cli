"""Dead-man switch for every cloud host rodeo launches.

A forgotten lab on a pay-per-use cloud bills until someone notices, so every
instance rodeo creates expires: ``provider.ttl_hours`` after launch (default
6) the guest powers itself off, and the instance is created with
InstanceInitiatedShutdownBehavior=terminate so that power-off becomes a
terminate (no stopped instance left billing for its disks). It cannot be
turned off, only lengthened for long workshops.

Two layers, because either alone is not enough: the AWS-side behaviour does
nothing without a shutdown, and a guest timer alone would leave a stopped
instance. The timer is a systemd OnCalendar at an absolute UTC time with
Persistent=true, so a host that was stopped past its expiry powers off again
as soon as it boots.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import ConfigError

DEFAULT_TTL_HOURS = 6
MAX_TTL_HOURS = 168  # a week: longer than any workshop, still bounded
TAG_EXPIRES_AT = "rodeo-expires-at"
TIMER_UNIT = "rodeo-deadman.timer"


def ttl_hours(config: dict[str, Any]) -> float:
    """``provider.ttl_hours``, validated; the default when unset."""
    raw = config.get("ttl_hours")
    if raw in (None, ""):
        return float(DEFAULT_TTL_HOURS)
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"provider.ttl_hours must be a number of hours, got {raw!r}") from None
    if not 0 < hours <= MAX_TTL_HOURS:
        raise ConfigError(
            f"provider.ttl_hours must be more than 0 and at most {MAX_TTL_HOURS} "
            f"(got {raw!r}); the dead-man switch cannot be turned off"
        )
    return hours


def expires_at(config: dict[str, Any], now: datetime | None = None) -> datetime:
    """UTC expiry for a host launched ``now``, to the second."""
    start = now or datetime.now(timezone.utc)
    return (start + timedelta(hours=ttl_hours(config))).astimezone(timezone.utc).replace(microsecond=0)


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cloud_config_files(expiry: datetime) -> str:
    """cloud-config ``write_files`` entries (indented list items) for the timer."""
    on_calendar = expiry.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return (
        "  - path: /etc/systemd/system/rodeo-deadman.service\n"
        "    owner: root:root\n"
        "    permissions: '0644'\n"
        "    content: |\n"
        "      [Unit]\n"
        "      Description=rodeo dead-man switch: power off this cloud host (AWS terminates it)\n"
        "      [Service]\n"
        "      Type=oneshot\n"
        "      ExecStart=/usr/bin/systemctl poweroff\n"
        f"  - path: /etc/systemd/system/{TIMER_UNIT}\n"
        "    owner: root:root\n"
        "    permissions: '0644'\n"
        "    content: |\n"
        "      [Unit]\n"
        "      Description=rodeo dead-man switch timer\n"
        "      [Timer]\n"
        f"      OnCalendar={on_calendar}\n"
        "      Persistent=true\n"
        "      AccuracySec=1s\n"
        "      [Install]\n"
        "      WantedBy=timers.target\n"
        "  - path: /etc/rodeo-expires-at\n"
        "    owner: root:root\n"
        "    permissions: '0644'\n"
        "    content: |\n"
        f"      {iso(expiry)}\n"
    )


def cloud_config_runcmd() -> str:
    """cloud-config ``runcmd`` items that arm the timer."""
    return (
        "  - [systemctl, daemon-reload]\n"
        f"  - [systemctl, enable, --now, {TIMER_UNIT}]\n"
    )
