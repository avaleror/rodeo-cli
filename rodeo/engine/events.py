"""Typed deploy events shared by every engine (native runner, lab-in-a-box).

Consumers (`commands/deploy.py` plain mode, `app.py` TUI) dispatch on these
types only — an engine that yields them plugs into both front ends unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeployEvent:
    pass


@dataclass
class PhaseStarted(DeployEvent):
    phase: str


@dataclass
class PhaseSkipped(DeployEvent):
    phase: str
    reason: str = ""  # "done" | "before_start"


@dataclass
class PhaseDone(DeployEvent):
    phase: str
    elapsed: float


@dataclass
class PhaseFailed(DeployEvent):
    phase: str
    rc: int
    message: str = ""


@dataclass
class LogLine(DeployEvent):
    line: str


@dataclass
class ProgressUpdate(DeployEvent):
    step: str
    elapsed: float
    total: float
    detail: str = ""


@dataclass
class DeployComplete(DeployEvent):
    pass


__all__ = [
    "DeployEvent",
    "PhaseStarted",
    "PhaseSkipped",
    "PhaseDone",
    "PhaseFailed",
    "LogLine",
    "ProgressUpdate",
    "DeployComplete",
]
