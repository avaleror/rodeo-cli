"""Abstract base for all rodeo profiles."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from ..engine.runner import DeployEvent, DeployRunner


class RodeoProfile(ABC):
    """Defines the phases, VM inventory, and phase dispatch for one rodeo type."""

    name: str
    phases: list[str]
    vm_names: list[str]
    ansible_phases: frozenset[str]

    @abstractmethod
    def default_cfg(self) -> dict:
        """Type-specific config defaults merged on top of base defaults."""

    @abstractmethod
    def run_phase(
        self,
        phase: str,
        runner: "DeployRunner",
        vars_file: Path,
    ) -> Iterator["DeployEvent"]:
        """Yield DeployEvents for one phase. Must set runner._last_rc."""
