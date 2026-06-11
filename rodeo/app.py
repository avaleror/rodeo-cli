"""RodeoApp — Textual TUI for deploy progress + VM serial logs."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Footer, Header

from .state import PHASES, is_phase_done, mark_phase_done, mark_phase_failed, reset_from
from .widgets.deploy_panel import DeployPanel
from .widgets.logs_panel import LogsPanel

_SERIAL_LOG_DIR = Path("/var/log/libvirt/qemu")
_VMS = ["harvester1", "harvester2", "harvester3", "rancher"]


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
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.ansible_root = ansible_root
        self.from_phase = from_phase
        self.install_collections = install_collections
        self.watch_only = watch_only
        self._ansible_proc: subprocess.Popen | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield DeployPanel()
            yield LogsPanel()
        yield Footer()

    def on_mount(self) -> None:
        self._start_log_tailers()
        if not self.watch_only:
            self._run_deploy()

    def action_quit(self) -> None:
        if self._ansible_proc and self._ansible_proc.poll() is None:
            self._ansible_proc.terminate()
        self.exit()

    # ---------- Deploy orchestration worker ----------

    @work(thread=True)
    def _run_deploy(self) -> None:
        cfg = self.cfg
        root = self.ansible_root
        from_phase = self.from_phase

        if from_phase:
            reset_from(from_phase)

        start_idx = PHASES.index(from_phase) if from_phase and from_phase in PHASES else 0

        # Install Ansible collections once
        if self.install_collections and start_idx <= PHASES.index("vms"):
            req_file = root / "ansible" / "requirements.yml"
            if req_file.exists():
                self._post_ansible_line("Installing Ansible collections...")
                subprocess.run(
                    ["ansible-galaxy", "collection", "install", "-r", str(req_file)],
                    capture_output=True,
                )

        for idx, phase in enumerate(PHASES):
            if idx < start_idx or is_phase_done(phase):
                self.post_message(_PhaseSkipped(phase))
                continue

            self.post_message(_PhaseStarted(phase))
            t0 = time.time()

            if phase == "kvm_host":
                ok = self._run_ansible_phase("kvm_host", cfg)
            elif phase == "vms":
                ok = self._run_ansible_phase("vms", cfg)
            elif phase == "cluster":
                ok = self._run_cluster(cfg, root)
            elif phase == "rancher":
                ok = self._run_rancher(cfg, root)
            elif phase == "finalise":
                ok = self._run_finalise(cfg)
            else:
                ok = True

            elapsed = time.time() - t0

            if ok:
                mark_phase_done(phase)
                self.post_message(_PhaseDone(phase, elapsed))
            else:
                mark_phase_failed(phase, "failed")
                self.post_message(_PhaseFailed(phase, 1))
                self.post_message(_DeployFailed(phase))
                return

        self.post_message(_DeployComplete())

    def _run_ansible_phase(self, tags: str, cfg: dict) -> bool:
        from .commands.deploy import _build_extra_vars

        root = self.ansible_root
        inventory = root / cfg["ansible"]["inventory"]
        playbook = root / "ansible" / "playbook.yml"
        cmd = [
            "ansible-playbook",
            "-i", str(inventory),
            str(playbook),
            "--tags", tags,
        ] + _build_extra_vars(cfg)

        self._ansible_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in self._ansible_proc.stdout:  # type: ignore[union-attr]
            self._post_ansible_line(line.rstrip())
        self._ansible_proc.wait()
        rc = self._ansible_proc.returncode
        self._ansible_proc = None
        return rc == 0

    def _run_cluster(self, cfg: dict, root: Path) -> bool:
        self._post_ansible_line("Starting firewalld...")
        subprocess.run(["systemctl", "start", "firewalld"], capture_output=True)
        subprocess.run(["firewall-cmd", "--reload"], capture_output=True)

        start_script = root / "deployer" / "lib" / "start-vms.sh"
        vip = cfg["network"]["vip"]

        if start_script.exists():
            env = {**os.environ, "HARVESTER_VIP": vip}
            self._ansible_proc = subprocess.Popen(
                [str(start_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            for line in self._ansible_proc.stdout:  # type: ignore[union-attr]
                self._post_ansible_line(line.rstrip())
            self._ansible_proc.wait()
            rc = self._ansible_proc.returncode
            self._ansible_proc = None
            return rc == 0
        return True

    def _run_rancher(self, cfg: dict, root: Path) -> bool:
        script = root / "deployer" / "lib" / "setup-rancher.sh"
        if not script.exists():
            self._post_ansible_line(f"⚠  {script} not found")
            return False

        creds = cfg.get("credentials", {})
        net = cfg["network"]
        ver = cfg.get("versions", {})
        env = {
            **os.environ,
            "RANCHER_VM_IP":         net.get("rancher_ip", "192.168.122.9"),
            "RANCHER_VERSION":       ver.get("rancher", "2.13.1"),
            "K3S_VERSION":           ver.get("k3s", "v1.31.4+k3s1"),
            "HARVESTER_VIP":         net.get("vip", "192.168.122.10"),
            "HARVESTER_OS_PASSWORD": creds.get("harvester_os_password", ""),
            "CERT_MANAGER_VERSION":  ver.get("cert_manager", "v1.16.2"),
            "LAB_ADMIN_PASSWORD":    creds.get("lab_admin_password", creds.get("harvester_os_password", "")),
        }
        self._ansible_proc = subprocess.Popen(
            [str(script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        for line in self._ansible_proc.stdout:  # type: ignore[union-attr]
            self._post_ansible_line(line.rstrip())
        self._ansible_proc.wait()
        rc = self._ansible_proc.returncode
        self._ansible_proc = None
        return rc == 0

    def _run_finalise(self, cfg: dict) -> bool:
        try:
            from .engine.libvirt import LibvirtDriver, RODEO_VMS
            with LibvirtDriver(cfg["libvirt"]["uri"]) as lv:
                for vm in RODEO_VMS:
                    try:
                        lv.set_autostart(vm, True)
                    except Exception:
                        pass
        except Exception as exc:
            self._post_ansible_line(f"⚠  autostart: {exc}")
        subprocess.run(["systemctl", "enable", "libvirt-guests"], capture_output=True)
        return True

    def _post_ansible_line(self, line: str) -> None:
        self.post_message(_AnsibleLine(line))

    # ---------- Log tailers (one worker per VM) ----------

    @work(thread=True)
    def _start_log_tailers(self) -> None:
        for vm in _VMS:
            self._tail_vm(vm)

    @work(thread=True)
    def _tail_vm(self, vm: str) -> None:
        log_file = _SERIAL_LOG_DIR / f"{vm}_serial.log"
        # Wait up to 5 minutes for the log file to appear
        deadline = time.time() + 300
        while not log_file.exists() and time.time() < deadline:
            time.sleep(5)
        if not log_file.exists():
            return
        with open(log_file) as f:
            f.seek(0, 2)  # start at end
            while True:
                line = f.readline()
                if line:
                    self.post_message(_LogLine(vm, line.rstrip()))
                else:
                    time.sleep(0.3)

    # ---------- Message handlers (main thread) ----------

    def on__phase_started(self, msg: _PhaseStarted) -> None:
        self.query_one(DeployPanel).set_phase_running(msg.phase)
        # Switch log panel to the first VM being worked on
        if msg.phase in ("vms", "cluster"):
            self.query_one(LogsPanel).switch_to("harvester1")
        elif msg.phase == "rancher":
            self.query_one(LogsPanel).switch_to("rancher")

    def on__phase_skipped(self, msg: _PhaseSkipped) -> None:
        self.query_one(DeployPanel).set_phase_skipped(msg.phase)

    def on__phase_done(self, msg: _PhaseDone) -> None:
        self.query_one(DeployPanel).set_phase_done(msg.phase, msg.elapsed)

    def on__phase_failed(self, msg: _PhaseFailed) -> None:
        self.query_one(DeployPanel).set_phase_failed(msg.phase)

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
