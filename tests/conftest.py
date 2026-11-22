import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A scratch copy of the repo's config/ to mutate in tests."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    return tmp_path
