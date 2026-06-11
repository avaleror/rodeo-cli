"""Left panel: phase status table + Ansible output stream."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Label, ProgressBar, RichLog

from ..state import PHASES


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
    #phase-progress {
        height: 1;
        margin: 0 1;
        display: none;
    }
    #ansible-log {
        height: 1fr;
        padding: 0 1;
    }
    """
    BORDER_TITLE = "Deploy"

    def __init__(self, phases: list[str] | None = None) -> None:
        super().__init__()
        self._phases = phases if phases is not None else PHASES

    def compose(self) -> ComposeResult:
        yield DataTable(id="phases-table", show_header=True, cursor_type="none")
        yield Label("", id="phase-sep")
        yield ProgressBar(id="phase-progress", total=100.0, show_eta=False)
        yield RichLog(id="ansible-log", highlight=True, markup=True, wrap=True, auto_scroll=True)

    def on_mount(self) -> None:
        table = self.query_one("#phases-table", DataTable)
        table.add_column("Phase",   key="phase",   width=12)
        table.add_column("Status",  key="status",  width=14)
        table.add_column("Elapsed", key="elapsed", width=8)
        for phase in self._phases:
            table.add_row(phase, Text("○ pending", style="dim"), "", key=phase)

    # --- Public update API called by RodeoApp message handlers ---

    def set_phase_running(self, phase: str) -> None:
        if phase in self._phases:
            t = self.query_one("#phases-table", DataTable)
            t.update_cell(phase, "status", Text("▶ running", style="bold yellow"))
        self.query_one("#phase-sep", Label).update(f" ▶ {phase}")

    def set_phase_done(self, phase: str, elapsed: float) -> None:
        if phase in self._phases:
            t = self.query_one("#phases-table", DataTable)
            m, s = divmod(int(elapsed), 60)
            t.update_cell(phase, "status",  Text("✓ done", style="green"))
            t.update_cell(phase, "elapsed", f"{m}:{s:02d}")
        self.query_one("#phase-progress", ProgressBar).display = False

    def set_phase_skipped(self, phase: str) -> None:
        if phase not in self._phases:
            return
        t = self.query_one("#phases-table", DataTable)
        t.update_cell(phase, "status", Text("— skip", style="dim"))

    def set_phase_failed(self, phase: str) -> None:
        # Pseudo-phases like "setup" (collection install) have no table row.
        if phase in self._phases:
            t = self.query_one("#phases-table", DataTable)
            t.update_cell(phase, "status", Text("✗ failed", style="bold red"))
        self.query_one("#phase-sep", Label).update(f" [red]✗ {phase} failed[/red]")

    def update_progress(self, step: str, elapsed: float, total: float, detail: str = "") -> None:
        bar = self.query_one("#phase-progress", ProgressBar)
        bar.display = True
        bar.update(total=total, progress=elapsed)
        m_e, s_e = divmod(int(elapsed), 60)
        m_t = int(total) // 60
        info = f"  {detail}" if detail else ""
        self.query_one("#phase-sep", Label).update(
            f" ▶ {step}{info}  {m_e}:{s_e:02d} / {m_t}:00"
        )

    def append_ansible(self, line: str) -> None:
        self.query_one("#ansible-log", RichLog).write(line)

    def set_done(self, vip: str, rancher_ip: str) -> None:
        self.query_one("#phase-sep", Label).update(
            f" [bold green]✓ Complete[/bold green]  "
            f"Harvester: https://{vip}  Rancher: https://{rancher_ip}:30002"
        )
        self.append_ansible(
            "[bold green]✓ Deployment complete.[/bold green] "
            "Credentials: admin / password in ~/.rodeo/secrets.yaml"
        )
