"""LabInABoxRunner — deploy through lab-in-a-box on a remote automation VM.

Speaks the same event protocol as DeployRunner (run() yields the types in
rodeo/engine/events.py, terminate() cancels), so `rodeo deploy` plain mode and
the TUI consume it unchanged. Selected with `engine: lab-in-a-box` in the plan
(see rodeo/engine/registry.py).

Targets lab-in-a-box release 1.8.0 — the Python-based contract introduced with
the 1.5.0 rewrite (https://github.com/SUSE-Technical-Marketing/lab-in-a-box):

  - `setup_lab.py [--keep] [--debug] <lab.json>` runs as root on the
    *automation VM* and exits 0 (clean) / 1 (preflight or any node/cluster/
    addon failure). `--keep` makes re-runs incremental; WITHOUT it every VM is
    destroyed and recreated, so rodeo passes it by default and only drops it
    on --force (mirroring upstream MCP's deploy_lab/rebuild_lab split).
  - `destroy_lab.py <lab.json>` tears the lab down but always exits 0 —
    failures are only visible as WARNING lines on the stream.
  - stdout is block-buffered off-TTY and `--debug` is what streams command
    output live, hence PYTHONUNBUFFERED=1 + --debug in the remote invocation.
  - Long silent windows are normal (per-cluster settle sleep is minutes) —
    silence is not a hang.

The automation VM is an operator-provided prerequisite (upstream's
install_automation_node_scripts.sh); rodeo verifies it in preflight rather
than installing it.
"""
from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Iterator

from ..labinabox import build_lab_json
from ..ssh import ssh_opts
from ..state import mark_phase_done, mark_phase_failed, reset_from
from .events import (
    DeployComplete,
    DeployEvent,
    LogLine,
    PhaseDone,
    PhaseFailed,
    PhaseSkipped,
    PhaseStarted,
)
from .stream import stream_command

# Lines worth surfacing in the PhaseFailed message, from the end-of-run
# LAB SUMMARY block and stderr (a closed set upstream: nodes/clusters/addons
# report `FAILED`, fatal messages are `ERROR:`-prefixed).
_FAILURE_MARKERS = ("FAILED", "ERROR", "✗")


def _overlay(cfg: dict) -> dict:
    return cfg.get("lab_in_a_box") or {}


def ssh_target_argv(cfg: dict) -> list[str]:
    """ssh argv prefix for the automation VM: ssh [-i key] OPTS user@host."""
    overlay = _overlay(cfg)
    argv = ["ssh"]
    identity = overlay.get("identity_file")
    if identity:
        argv += ["-i", str(identity)]
    argv += ssh_opts()
    argv.append(overlay["automation_host"])
    return argv


def remote_lab_path(cfg: dict) -> str:
    overlay = _overlay(cfg)
    plan = cfg.get("name", "default")
    remote_dir = overlay.get("remote_dir") or f"/root/rodeo-labs/{plan}"
    return f"{remote_dir.rstrip('/')}/lab.json"


def destroy_remote_lab(cfg: dict, run=subprocess.run) -> tuple[int, list[str]]:
    """Run destroy_lab.py on the automation VM. Returns (rc, output lines).

    destroy_lab.py always exits 0 — per-VM destroy failures only appear as
    WARNING lines, so callers must show the output, not just the rc.
    """
    cmd = ssh_target_argv(cfg) + [f"destroy_lab.py {remote_lab_path(cfg)}"]
    result = run(cmd, capture_output=True, text=True, timeout=1800)
    lines = (result.stdout + result.stderr).splitlines()
    return result.returncode, [line.rstrip() for line in lines if line.strip()]


class LabInABoxRunner:
    """Render the plan to lab.json, push it, and stream setup_lab.py."""

    # Class-level phase list (no profile involved): consumers that render a
    # phase table (TUI, `--from` validation) read it from here.
    phases = ["render", "deploy"]
    # Work happens on the automation VM — no local serial logs to tail.
    remote = True

    def __init__(
        self,
        cfg: dict,
        root: Path | None = None,
        from_phase: str | None = None,
        install_collections: bool = True,  # noqa: ARG002 — native-engine knob
        force: bool = False,
        include_guarded: bool = False,  # noqa: ARG002 — native-engine knob
        ansible_verbose: int = 0,  # noqa: ARG002 — native-engine knob
        reconcile: bool = True,  # noqa: ARG002 — upstream --keep reconciles
    ) -> None:
        self.cfg = cfg
        self.root = root
        self.from_phase = from_phase
        self.force = force
        self._plan_name = cfg.get("name", "default")
        self._proc: subprocess.Popen | None = None
        self._last_rc: int = 0
        self.stop = threading.Event()

    # ---------- cancellation (same semantics as DeployRunner) ----------

    def terminate(self) -> None:
        import os
        import signal

        self.stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                self._proc.terminate()

    def _set_proc(self, proc: subprocess.Popen | None) -> None:
        self._proc = proc

    # ---------- pipeline ----------

    @property
    def _log_file(self) -> Path:
        from ..paths import rodeo_logs_dir

        log_dir = rodeo_logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{self._plan_name}.log"

    @property
    def _local_lab_json(self) -> Path:
        from ..paths import rodeo_dir

        out_dir = rodeo_dir() / "labinabox"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{self._plan_name}.lab.json"

    def run(self) -> Iterator[DeployEvent]:
        if self.from_phase:
            reset_from(self.from_phase, self._plan_name, self.phases)
        start_idx = (
            self.phases.index(self.from_phase)
            if self.from_phase in self.phases
            else 0
        )

        for idx, phase in enumerate(self.phases):
            if idx < start_idx:
                yield PhaseSkipped(phase, "before_start")
                continue
            # No "done" skip: setup_lab.py --keep is the reconciler, so a
            # re-run is cheap and picks up plan edits — unlike the native
            # engine there is no expensive phase worth caching.
            if self.stop.is_set():
                mark_phase_failed(phase, "cancelled", self._plan_name)
                yield PhaseFailed(phase, 130, "cancelled")
                return

            yield PhaseStarted(phase)
            t0 = time.monotonic()
            message = ""
            try:
                if phase == "render":
                    yield from self._phase_render()
                else:
                    message = yield from self._phase_deploy()
            except Exception as exc:
                self._last_rc = 1
                message = str(exc)
                yield LogLine(f"  ✗  {phase}: {exc}")

            elapsed = time.monotonic() - t0
            if self._last_rc == 0:
                mark_phase_done(phase, self._plan_name)
                yield PhaseDone(phase, elapsed)
            else:
                mark_phase_failed(
                    phase, message or f"{phase} exited {self._last_rc}", self._plan_name
                )
                yield PhaseFailed(phase, self._last_rc, message or f"{phase} failed")
                return

        yield DeployComplete()

    # ---------- phases ----------

    def _phase_render(self) -> Iterator[DeployEvent]:
        """Build lab.json from the plan and push it to the automation VM."""
        import json

        lab, warnings = build_lab_json(self.cfg)
        for warning in warnings:
            yield LogLine(f"  ⚠  {warning}")

        local = self._local_lab_json
        local.write_text(json.dumps(lab, indent=2) + "\n")
        local.chmod(0o600)  # sections: may carry addon credentials
        yield LogLine(f"  ✓  rendered {local}")

        remote_path = remote_lab_path(self.cfg)
        remote_dir = remote_path.rsplit("/", 1)[0]
        rc = yield from self._stream(
            ssh_target_argv(self.cfg) + [f"mkdir -p {remote_dir}"]
        )
        if rc != 0:
            self._last_rc = rc
            return

        overlay = _overlay(self.cfg)
        scp = ["scp"]
        if overlay.get("identity_file"):
            scp += ["-i", str(overlay["identity_file"])]
        scp += ssh_opts()
        scp += [str(local), f"{overlay['automation_host']}:{remote_path}"]
        rc = yield from self._stream(scp)
        self._last_rc = rc
        if rc == 0:
            yield LogLine(f"  ✓  pushed to {overlay['automation_host']}:{remote_path}")

    def _phase_deploy(self) -> Iterator[DeployEvent]:
        """Stream setup_lab.py on the automation VM; return a failure summary."""
        overlay = _overlay(self.cfg)
        flags = ""
        # --force asks upstream for its destroy-and-recreate run; the default
        # keeps existing, matching VMs (incremental).
        if overlay.get("keep", True) and not self.force:
            flags += " --keep"
        if overlay.get("debug", True):
            flags += " --debug"
        remote_cmd = f"PYTHONUNBUFFERED=1 setup_lab.py{flags} {remote_lab_path(self.cfg)}"
        cmd = ssh_target_argv(self.cfg) + [remote_cmd]

        tail: deque[str] = deque(maxlen=120)
        rc = yield from self._stream(cmd, tail=tail)
        self._last_rc = rc
        if rc == 0:
            return ""
        failures = [
            line.strip()
            for line in tail
            if any(marker in line for marker in _FAILURE_MARKERS)
        ]
        return "; ".join(failures[-8:]) or "setup_lab.py failed"

    def _stream(
        self, cmd: list[str], tail: deque[str] | None = None
    ) -> Iterator[DeployEvent]:
        gen = stream_command(cmd, self._log_file, on_proc=self._set_proc)
        while True:
            try:
                event = next(gen)
            except StopIteration as done:
                return done.value
            if tail is not None and isinstance(event, LogLine):
                tail.append(event.line)
            yield event
