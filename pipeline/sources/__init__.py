"""Fetcher registry: maps a source `kind` (from sources.yaml) to its module.

A fetcher module provides:
  request_spec(cfg, src) -> dict   # everything that determines the result
  fetch(cfg, src, entry) -> dict   # download into the cache, return the record
"""

from __future__ import annotations

from types import ModuleType

from . import wfs

# Kinds fetched together by the coverage-driven terrain coordinator
# (sources/terrain.py) rather than one by one.
TERRAIN_KINDS = ("lidarbc", "stac_cog")

FETCHERS: dict[str, ModuleType] = {
    "wfs": wfs,
}

# Kinds that exist in sources.yaml but whose fetchers arrive in later milestones.
PLANNED: dict[str, str] = {
    "geofabrik": "milestone 3 (vectors)",
    "bcgnws": "milestone 3 (vectors)",
    "geomag": "milestone 4 (render)",
}


def get(kind: str) -> ModuleType | None:
    return FETCHERS.get(kind)
