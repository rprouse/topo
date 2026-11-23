#!/usr/bin/env python3
"""Fail if downloaded geodata or large binaries are tracked by git.

Usage: check_no_geodata.py            # all tracked files
       check_no_geodata.py --staged   # files staged for commit (pre-commit hook)

Hand-authored files that legitimately live in the repo (the corrections
layer, the QGIS project and its auxiliary storage) are allow-listed, but are
still subject to the size limit.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024

FORBIDDEN = [
    "*.tif", "*.tiff", "*.vrt", "*.asc", "*.dem", "*.img", "*.nc", "*.xyz",
    "*.las", "*.laz", "*.copc",
    "*.pbf", "*.osm", "*.o5m", "*.osc",
    "*.shp", "*.shx", "*.dbf", "*.prj", "*.cpg",
    "*.gpkg", "*.sqlite", "*.fgb", "*.parquet",
    "*.geojson", "*.geojsonl", "*.kml", "*.kmz", "*.gpx", "*.gml",
    "*.zip", "*.7z", "*.tar", "*.gz", "*.bz2", "*.xz",
    "*.pdf",
    ".cache/*", "build/*",
]

ALLOWED = [
    "authored/*.gpkg",
    "qgis/*.qgz",
    "qgis/*.qgd",
    "qgis/*_attachments.zip",   # QGIS project attachments (style database)
    "tests/fixtures/*",
    "docs/*.pdf",
]


def files(staged: bool) -> list[str]:
    root = Path(__file__).resolve().parent.parent
    cmd = ["git", "-c", f"safe.directory={root}", "-C", str(root)]
    cmd += ["diff", "--cached", "--name-only", "--diff-filter=ACMR"] if staged else ["ls-files"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f]


def main() -> int:
    staged = "--staged" in sys.argv
    root = Path(__file__).resolve().parent.parent
    problems = []
    for f in files(staged):
        lower = f.lower()
        if any(fnmatch.fnmatch(lower, pat) for pat in FORBIDDEN) and not any(
            fnmatch.fnmatch(f, pat) for pat in ALLOWED
        ):
            problems.append(f"{f}: looks like downloaded geodata or an archive")
        p = root / f
        if p.exists() and p.stat().st_size > MAX_BYTES:
            problems.append(f"{f}: {p.stat().st_size / 1e6:.1f} MB exceeds {MAX_BYTES / 1e6:.0f} MB limit")
    if problems:
        print("Geodata check failed. Source data must be fetched at build time, not committed:")
        print("\n".join(f"  - {x}" for x in problems))
        return 1
    print(f"Geodata check passed ({'staged' if staged else 'tracked'} files).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
