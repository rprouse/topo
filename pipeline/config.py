"""Load and validate config/map.yaml and config/sources.yaml.

The loaded config is a plain, immutable view over the YAML plus a few derived
values (the AOI bounding box in the map CRS, in WGS84 and in any source's
native CRS). Nothing about the map is hard-coded outside these files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

Bbox = tuple[float, float, float, float]  # xmin, ymin, xmax, ymax

NAMED_EXTENTS = ("full", "west", "east", "north", "south")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Side:
    id: str
    title: str
    extent: Bbox  # in map CRS


@dataclass(frozen=True)
class Licence:
    id: str
    name: str
    url: str
    attribution: str


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    kind: str
    group: str
    licence: str
    credit: str
    verified: str | None
    raw: dict[str, Any] = field(repr=False, compare=False)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)


@dataclass(frozen=True)
class Config:
    root: Path
    map: dict[str, Any] = field(repr=False)
    crs: str
    scale: int
    edition: str
    aoi: Bbox            # map CRS, no buffer
    aoi_buffered: Bbox   # map CRS, with buffer
    sides: tuple[Side, ...]
    licences: dict[str, Licence]
    sources: dict[str, Source]

    @property
    def cache_dir(self) -> Path:
        return self.root / ".cache"

    @property
    def build_dir(self) -> Path:
        return self.root / "build"

    def bbox_in(self, crs: str, buffered: bool = True) -> Bbox:
        """The AOI (optionally buffered) as an envelope in another CRS."""
        return transform_bbox(self.aoi_buffered if buffered else self.aoi, self.crs, crs)


# --------------------------------------------------------------------------
# CRS helpers (GDAL/OSR is always present in the QGIS image)
# --------------------------------------------------------------------------

def _srs(crs: str):
    from osgeo import osr

    osr.UseExceptions()
    srs = osr.SpatialReference()
    try:
        srs.SetFromUserInput(crs)
    except RuntimeError as e:
        raise ConfigError(f"Unknown CRS: {crs!r} ({e})") from None
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)  # x=lon/easting
    return srs


def crs_name(crs: str) -> str:
    return _srs(crs).GetName()


def transform_point(x: float, y: float, src: str, dst: str) -> tuple[float, float]:
    from osgeo import osr

    ct = osr.CoordinateTransformation(_srs(src), _srs(dst))
    tx, ty, _ = ct.TransformPoint(x, y)
    return tx, ty


def transform_bbox(bbox: Bbox, src: str, dst: str, densify: int = 21) -> Bbox:
    """Envelope of a bbox after reprojection, densifying the edges so curved
    edges in the target CRS are fully enclosed."""
    if src == dst:
        return bbox
    from osgeo import osr

    ct = osr.CoordinateTransformation(_srs(src), _srs(dst))
    xmin, ymin, xmax, ymax = bbox
    pts = []
    for i in range(densify):
        t = i / (densify - 1)
        x = xmin + (xmax - xmin) * t
        y = ymin + (ymax - ymin) * t
        pts += [(x, ymin), (x, ymax), (xmin, y), (xmax, y)]
    out = [ct.TransformPoint(px, py)[:2] for px, py in pts]
    xs = [p[0] for p in out]
    ys = [p[1] for p in out]
    return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _require(d: dict, key: str, ctx: str) -> Any:
    if key not in d or d[key] is None:
        raise ConfigError(f"{ctx}: missing required key '{key}'")
    return d[key]


def _side_extent(spec: Any, aoi: Bbox, ctx: str) -> Bbox:
    xmin, ymin, xmax, ymax = aoi
    xmid, ymid = (xmin + xmax) / 2, (ymin + ymax) / 2
    if isinstance(spec, str):
        named = {
            "full": aoi,
            "west": (xmin, ymin, xmid, ymax),
            "east": (xmid, ymin, xmax, ymax),
            "south": (xmin, ymin, xmax, ymid),
            "north": (xmin, ymid, xmax, ymax),
        }
        if spec not in named:
            raise ConfigError(f"{ctx}: extent must be one of {NAMED_EXTENTS} or a bbox, got {spec!r}")
        return named[spec]
    if isinstance(spec, (list, tuple)) and len(spec) == 4:
        b = tuple(float(v) for v in spec)
        if not (b[0] < b[2] and b[1] < b[3]):
            raise ConfigError(f"{ctx}: extent bbox must be [xmin, ymin, xmax, ymax]")
        return b  # type: ignore[return-value]
    raise ConfigError(f"{ctx}: invalid extent {spec!r}")


def load(root: Path | str = ".") -> Config:
    root = Path(root).resolve()
    map_path = root / "config" / "map.yaml"
    src_path = root / "config" / "sources.yaml"
    m = yaml.safe_load(map_path.read_text(encoding="utf-8")) or {}
    s = yaml.safe_load(src_path.read_text(encoding="utf-8")) or {}

    crs = str(_require(m, "crs", "map.yaml"))
    _srs(crs)  # validate early
    scale = int(_require(m, "scale", "map.yaml"))
    edition = str(_require(m, "edition", "map.yaml"))

    aoi_cfg = _require(m, "aoi", "map.yaml")
    lon, lat = (float(v) for v in _require(aoi_cfg, "center", "map.yaml aoi"))
    w = float(_require(aoi_cfg, "width_m", "map.yaml aoi"))
    h = float(_require(aoi_cfg, "height_m", "map.yaml aoi"))
    buf = float(aoi_cfg.get("buffer_m", 0))
    cx, cy = transform_point(lon, lat, "EPSG:4326", crs)
    # Snap the AOI to whole metres so it is stable across GDAL/PROJ versions.
    cx, cy = round(cx), round(cy)
    aoi = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    aoi_buffered = (aoi[0] - buf, aoi[1] - buf, aoi[2] + buf, aoi[3] + buf)

    sheet = _require(m, "sheet", "map.yaml")
    sides = []
    for i, sd in enumerate(_require(sheet, "sides", "map.yaml sheet")):
        ctx = f"map.yaml sheet.sides[{i}]"
        sides.append(Side(
            id=str(_require(sd, "id", ctx)),
            title=str(sd.get("title", "")),
            extent=_side_extent(_require(sd, "extent", ctx), aoi, ctx),
        ))
    if not sides:
        raise ConfigError("map.yaml: sheet.sides must list at least one side")

    licences = {}
    for lid, ld in (_require(s, "licences", "sources.yaml") or {}).items():
        ctx = f"sources.yaml licences.{lid}"
        licences[lid] = Licence(
            id=lid,
            name=str(_require(ld, "name", ctx)),
            url=str(_require(ld, "url", ctx)),
            attribution=" ".join(str(_require(ld, "attribution", ctx)).split()),
        )

    sources = {}
    for sid, sd in (_require(s, "sources", "sources.yaml") or {}).items():
        ctx = f"sources.yaml sources.{sid}"
        lic = str(_require(sd, "licence", ctx))
        if lic not in licences:
            raise ConfigError(f"{ctx}: licence '{lic}' is not defined under licences")
        if "trailforks" in sid.lower() or "trailforks" in str(sd.get("endpoint", "")).lower():
            raise ConfigError(f"{ctx}: Trailforks data must not be used (terms prohibit it)")
        verified = sd.get("verified")
        sources[sid] = Source(
            id=sid,
            title=str(_require(sd, "title", ctx)),
            kind=str(_require(sd, "kind", ctx)),
            group=str(sd.get("group", "")),
            licence=lic,
            credit=str(sd.get("credit", "")),
            verified=str(verified) if verified else None,
            raw=dict(sd),
        )

    return Config(
        root=root, map=m, crs=crs, scale=scale, edition=edition,
        aoi=aoi, aoi_buffered=aoi_buffered, sides=tuple(sides),
        licences=licences, sources=sources,
    )
