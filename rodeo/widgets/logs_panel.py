"""Right panel: all VM serial console logs visible simultaneously in a split view."""
from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog


class LogsPanel(Vertical):
    DEFAULT_CSS = """
    LogsPanel {
        width: 1fr;
        border: round $accent;
        padding: 0;
    }
    LogsPanel RichLog {
        height: 1fr;
        border: tall $panel;
        padding: 0 1;
    }
    """
    BORDER_TITLE = "VM Serial Logs"

    def __init__(self, vms: list[str] | None = None) -> None:
        super().__init__()
        self._vms = vms or ["harvester1", "harvester2", "harvester3", "rancher"]
        self._vm_start: dict[str, float] = {}

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick_vm_timers)

    def _tick_vm_timers(self) -> None:
        for vm, t0 in self._vm_start.items():
            elapsed = time.monotonic() - t0
            m, s = divmod(int(elapsed), 60)
            try:
                self.query_one(f"#log-{vm}", RichLog).border_title = f"{vm}  {m:02d}:{s:02d}"
            except Exception:
                pass

    def compose(self) -> ComposeResult:
        for vm in self._vms:
            log = RichLog(
                id=f"log-{vm}",
                highlight=False,
                markup=False,
                wrap=False,
                auto_scroll=True,
                max_lines=500,
            )
            log.border_title = vm
            yield log

    def append_log(self, vm: str, line: str) -> None:
        if vm not in self._vm_start:
            self._vm_start[vm] = time.monotonic()
        try:
            self.query_one(f"#log-{vm}", RichLog).write(line)
        except Exception:
            pass

    def append_logs(self, vm: str, lines: list[str]) -> None:
        if vm not in self._vm_start:
            self._vm_start[vm] = time.monotonic()
        try:
            log = self.query_one(f"#log-{vm}", RichLog)
            for line in lines:
                log.write(line)
        except Exception:
            pass

    def switch_to(self, vm: str) -> None:
        pass  # no-op: split view shows all VMs simultaneously
