"""Right panel: tabbed VM serial console logs."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog, TabbedContent, TabPane

class LogsPanel(Vertical):
    DEFAULT_CSS = """
    LogsPanel {
        width: 1fr;
        border: round $accent;
        padding: 0;
    }
    LogsPanel RichLog {
        height: 1fr;
        padding: 0 1;
    }
    """
    BORDER_TITLE = "VM Serial Logs"

    def __init__(self, vms: list[str] | None = None) -> None:
        super().__init__()
        self._vms = vms or ["harvester1", "harvester2", "harvester3", "rancher"]

    def compose(self) -> ComposeResult:
        with TabbedContent():
            for vm in self._vms:
                with TabPane(vm, id=f"tab-{vm}"):
                    yield RichLog(
                        id=f"log-{vm}",
                        highlight=False,
                        markup=False,
                        wrap=False,
                        auto_scroll=True,
                    )

    def append_log(self, vm: str, line: str) -> None:
        try:
            self.query_one(f"#log-{vm}", RichLog).write(line)
        except Exception:
            pass

    def switch_to(self, vm: str) -> None:
        try:
            self.query_one(TabbedContent).active = f"tab-{vm}"
        except Exception:
            pass
