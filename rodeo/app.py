"""RodeoApp — Textual TUI for deploy progress + VM serial logs."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Footer, Header

from .engine.runner import (
    DeployComplete,
    DeployRunner,
    LogLine,
    PhaseDone,
    PhaseFailed,
    PhaseSkipped,
    PhaseStarted,
    ProgressUpdate,
)
from .widgets.deploy_panel import DeployPanel
from .widgets.logs_panel import LogsPanel

_SERIAL_LOG_DIR = Path("/var/log/libvirt/qemu")


# ---------- Messages (thread-safe inter-component communication) ----------

class _PhaseStarted(Message):
    def __init__(self, phase: str) -> None:
        super().__init__()
        self.phase = phase


class _PhaseSkipped(Message):
    def __init__(self, phase: str) -> None:
        super().__init__()
        self.phase = phase


class _PhaseDone(Message):
    def __init__(self, phase: str, elapsed: float) -> None:
        super().__init__()
        self.phase = phase
        self.elapsed = elapsed


class _PhaseFailed(Message):
    def __init__(self, phase: str, rc: int) -> None:
        super().__init__()
        self.phase = phase
        self.rc = rc


class _AnsibleLine(Message):
    def __init__(self, line: str) -> None:
        super().__init__()
        self.line = line


class _LogLine(Message):
    def __init__(self, vm: str, line: str) -> None:
        super().__init__()
        self.vm = vm
        self.line = line


class _ProgressUpdate(Message):
    def __init__(self, step: str, elapsed: float, total: float, detail: str = "") -> None:
        super().__init__()
        self.step = step
        self.elapsed = elapsed
        self.total = total
        self.detail = detail


class _DeployComplete(Message):
    pass


class _DeployFailed(Message):
    def __init__(self, phase: str) -> None:
        super().__init__()
        self.phase = phase


# ---------- App ----------

class RodeoApp(App):
    TITLE = "rodeo"
    CSS = """
    Screen {
        layout: horizontal;
    }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def __init__(
        self,
        cfg: dict,
        ansible_root: Path,
        from_phase: str | None = None,
        install_collections: bool = True,
        watch_only: bool = False,
        force: bool = False,
        include_guarded: bool = False,
        ansible_verbose: int = 0,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.ansible_root = ansible_root
        self.from_phase = from_phase
        self.install_collections = install_collections
        self.watch_only = watch_only
        self.force = force
        self.include_guarded = include_guarded
        self.ansible_verbose = ansible_verbose
        self.exit_code: int = 0
        self._runner: DeployRunner | None = None
        self._stop_tailers = threading.Event()

    def compose(self) -> ComposeResult:
        from .profiles import get_profile
        profile = get_profile(self.cfg.get("type", "suse-virt"))
        vms = list(self.cfg.get("vms", {}).keys()) or profile.vm_names
        yield Header(show_clock=True)
        with Horizontal():
            yield DeployPanel(phases=profile.phases)
            yield LogsPanel(vms=vms)
        yield Footer()

    def on_mount(self) -> None:
        for vm in self.cfg.get("vms", {}).keys():
            self._tail_vm(vm)
        if not self.watch_only:
            self._run_deploy()

    def action_quit(self) -> None:
        self._stop_tailers.set()
        if self._runner:
            # Quitting mid-deploy is an aborted run, not a success.
            self.exit_code = 130
            self._runner.terminate()
        self.exit()

    # ---------- Deploy worker ----------

    @work(thread=True)
    def _run_deploy(self) -> None:
        self._runner = DeployRunner(
            cfg=self.cfg,
            root=self.ansible_root,
            from_phase=self.from_phase,
            install_collections=self.install_collections,
            force=self.force,
            include_guarded=self.include_guarded,
            ansible_verbose=self.ansible_verbose,
        )
        for event in self._runner.run():
            if isinstance(event, PhaseStarted):
                self.post_message(_PhaseStarted(event.phase))
            elif isinstance(event, PhaseSkipped):
                self.post_message(_PhaseSkipped(event.phase))
            elif isinstance(event, PhaseDone):
                self.post_message(_PhaseDone(event.phase, event.elapsed))
            elif isinstance(event, PhaseFailed):
                self.exit_code = event.rc or 1
                self.post_message(_PhaseFailed(event.phase, event.rc))
                self.post_message(_DeployFailed(event.phase))
            elif isinstance(event, ProgressUpdate):
                self.post_message(_ProgressUpdate(
                    event.step, event.elapsed, event.total, event.detail
                ))
            elif isinstance(event, LogLine):
                self.post_message(_AnsibleLine(event.line))
            elif isinstance(event, DeployComplete):
                self.post_message(_DeployComplete())
        self._runner = None

    # ---------- Log tailers (one worker per VM) ----------

    @work(thread=True)
    def _tail_vm(self, vm: str) -> None:
        log_file = _SERIAL_LOG_DIR / f"{vm}_serial.log"
        # VMs boot during cluster phase, which starts 12+ min into the deploy.
        # Wait up to 2 hours so tailers are alive when VMs actually boot.
        deadline = time.monotonic() + 7200
        while not log_file.exists():
            if self._stop_tailers.is_set() or time.monotonic() > deadline:
                return
            time.sleep(5)
        with open(log_file) as f:
            f.seek(0, 2)
            while not self._stop_tailers.is_set():
                line = f.readline()
                if line:
                    self.post_message(_LogLine(vm, line.rstrip()))
                else:
                    time.sleep(0.3)

    # ---------- Message handlers (main thread) ----------

    def on__phase_started(self, msg: _PhaseStarted) -> None:
        self.query_one(DeployPanel).set_phase_running(msg.phase)
        vms = list(self.cfg.get("vms", {}).keys())
        harvester_vm = next((n for n in vms if n != "rancher"), vms[0] if vms else "harvester1")
        rancher_vm = "rancher" if "rancher" in vms else (vms[-1] if vms else "rancher")
        if msg.phase in ("vms", "cluster"):
            self.query_one(LogsPanel).switch_to(harvester_vm)
        elif msg.phase == "rancher":
            self.query_one(LogsPanel).switch_to(rancher_vm)

    def on__phase_skipped(self, msg: _PhaseSkipped) -> None:
        self.query_one(DeployPanel).set_phase_skipped(msg.phase)

    def on__phase_done(self, msg: _PhaseDone) -> None:
        self.query_one(DeployPanel).set_phase_done(msg.phase, msg.elapsed)

    def on__phase_failed(self, msg: _PhaseFailed) -> None:
        self.query_one(DeployPanel).set_phase_failed(msg.phase)

    def on__progress_update(self, msg: _ProgressUpdate) -> None:
        self.query_one(DeployPanel).update_progress(
            msg.step, msg.elapsed, msg.total, msg.detail
        )

    def on__ansible_line(self, msg: _AnsibleLine) -> None:
        self.query_one(DeployPanel).append_ansible(msg.line)

    def on__log_line(self, msg: _LogLine) -> None:
        self.query_one(LogsPanel).append_log(msg.vm, msg.line)

    def on__deploy_complete(self, _: _DeployComplete) -> None:
        net = self.cfg["network"]
        self.query_one(DeployPanel).set_done(net["vip"], net["rancher_ip"])
        self.sub_title = "Complete"

    def on__deploy_failed(self, msg: _DeployFailed) -> None:
        self.sub_title = f"Failed at: {msg.phase}"
