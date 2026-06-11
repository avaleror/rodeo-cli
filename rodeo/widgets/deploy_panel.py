"""Left panel: phase status table + Ansible output stream."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Label, RichLog

from ..state import PHASES

_PHASE_ORDER = PHASES  # ["kvm_host", "vms", "cluster", "rancher", "finalise"]


class DeployPanel(Vertical):
    DEFAULT_CSS = """
    DeployPanel {
        width: 40%;
        border: round $accent;
        padding: 0;
    }
    #phases-table {
        height: auto;
        max-height: 9;
        margin: 0;
        padding: 0 1;
    }
    #phase-sep {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    #ansible-log {
        height: 1fr;
        padding: 0 1;
    }
    """
    BORDER_TITLE = "Deploy"

    def compose(self) -> ComposeResult:
        yield DataTable(id="phases-table", show_header=True, cursor_type="none")
        yield Label("", id="phase-sep")
        yield RichLog(id="ansible-log", highlight=True, markup=True, wrap=True, auto_scroll=True)

    def on_mount(self) -> None:
        table = self.query_one("#phases-table", DataTable)
        table.add_column("Phase",   key="phase",   width=12)
        table.add_column("Status",  key="status",  width=14)
        table.add_column("Elapsed", key="elapsed", width=8)
        for phase in _PHASE_ORDER:
            table.add_row(phase, Text("○ pending", style="dim"), "", key=phase)

    # --- Public update API called by RodeoApp message handlers ---

    def set_phase_running(self, phase: str) -> None:
        t = self.query_one("#phases-table", DataTable)
        t.update_cell(phase, "status", Text("▶ running", style="bold yellow"))
        self.query_one("#phase-sep", Label).update(f" ▶ {phase}")

    def set_phase_done(self, phase: str, elapsed: float) -> None:
        t = self.query_one("#phases-table", DataTable)
        m, s = divmod(int(elapsed), 60)
        t.update_cell(phase, "status",  Text("✓ done", style="green"))
        t.update_cell(phase, "elapsed", f"{m}:{s:02d}")

    def set_phase_skipped(self, phase: str) -> None:
        t = self.query_one("#phases-table", DataTable)
        t.update_cell(phase, "status", Text("— skip", style="dim"))

    def set_phase_failed(self, phase: str) -> None:
        t = self.query_one("#phases-table", DataTable)
        t.update_cell(phase, "status", Text("✗ failed", style="bold red"))
        self.query_one("#phase-sep", Label).update(f" [red]✗ {phase} failed[/red]")

    def append_ansible(self, line: str) -> None:
        self.query_one("#ansible-log", RichLog).write(line)

    def set_done(self, vip: str, rancher_ip: str) -> None:
        self.query_one("#phase-sep", Label).update(
            f" [bold green]✓ Complete[/bold green]  "
            f"Harvester: https://{vip}  Rancher: https://{rancher_ip}:30002"
        )
