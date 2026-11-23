"""Tool and environment versions recorded in the manifest."""

from __future__ import annotations

import os
import platform
import subprocess
from importlib import metadata


def _pkg(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _qgis() -> str | None:
    try:
        from qgis.core import Qgis  # noqa: PLC0415

        return Qgis.version()
    except Exception:  # noqa: BLE001 - QGIS is optional outside the image
        return None


def _git_commit(root) -> str | None:
    if os.environ.get("GIT_COMMIT"):
        return os.environ["GIT_COMMIT"]
    try:
        return subprocess.run(
            ["git", "-c", f"safe.directory={root}", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def _cmd_version(*cmd: str) -> str | None:
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True, check=True).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def collect(root) -> dict[str, str | None]:
    from osgeo import gdal, osr

    return {
        "python": platform.python_version(),
        "gdal": gdal.__version__,
        "proj": ".".join(str(v) for v in (osr.GetPROJVersionMajor(), osr.GetPROJVersionMinor(), osr.GetPROJVersionMicro())),
        "qgis": _qgis(),
        "pyyaml": _pkg("PyYAML"),
        "requests": _pkg("requests"),
        "numpy": _pkg("numpy"),
        "scipy": _pkg("scipy"),
        "pygeomag": _pkg("pygeomag"),
        "pypdf": _pkg("pypdf"),
        "ghostscript": _cmd_version("gs", "--version"),
        "docker_image": os.environ.get("PIPELINE_IMAGE"),
        "git_commit": _git_commit(root),
        "git_dirty": os.environ.get("GIT_DIRTY"),
    }
