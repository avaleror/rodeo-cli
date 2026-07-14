"""Engine poll loops must use cancellable _sleep(), not raw time.sleep()."""
from __future__ import annotations

from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).parent.parent / "rodeo" / "engine"


@pytest.mark.parametrize("path", sorted(ENGINE_DIR.glob("*.py")), ids=lambda p: p.name)
def test_engine_modules_avoid_raw_time_sleep(path: Path):
    source = path.read_text()
    assert "time.sleep" not in source, (
        f"{path.name} must use cancellable _sleep(), not time.sleep"
    )