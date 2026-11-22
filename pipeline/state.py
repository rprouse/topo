"""Per-stage state in build/state/<stage>.json.

Each stage records the hash of its inputs and a summary of what it produced.
A stage whose input hash is unchanged (and whose outputs still exist) is
skipped. The manifest stage assembles these files into build/manifest.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .cache import utc_now


def state_path(build_dir: Path, stage: str) -> Path:
    return build_dir / "state" / f"{stage}.json"


def read(build_dir: Path, stage: str) -> dict[str, Any] | None:
    p = state_path(build_dir, stage)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write(build_dir: Path, stage: str, inputs_hash: str, data: dict[str, Any]) -> Path:
    p = state_path(build_dir, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"stage": stage, "inputs_hash": inputs_hash, "completed_at": utc_now(), **data}
    p.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
    return p


def is_current(build_dir: Path, stage: str, inputs_hash: str, outputs: list[Path] = ()) -> bool:
    prev = read(build_dir, stage)
    if not prev or prev.get("inputs_hash") != inputs_hash:
        return False
    return all(Path(o).exists() for o in outputs)
