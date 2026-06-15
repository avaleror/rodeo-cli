"""Lab directory auto-detection (walk-up)."""
from __future__ import annotations

from rodeo.config import find_lab_dir


def test_finds_lab_from_subdir(tmp_path):
    lab = tmp_path / "mylab"
    (lab / "certs").mkdir(parents=True)
    (lab / "rodeo-plan.yaml").write_text("name: x\n")
    assert find_lab_dir(lab / "certs") == lab.resolve()


def test_finds_lab_by_definition_marker(tmp_path):
    lab = tmp_path / "lab2"
    lab.mkdir()
    (lab / "definition.yaml").write_text("definition: {}\n")
    assert find_lab_dir(lab) == lab.resolve()


def test_returns_none_when_no_marker(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_lab_dir(empty) is None
