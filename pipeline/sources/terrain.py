"""Coverage-driven terrain fetch.

Terrain sources are fetched together, in the priority order given by
`terrain.sources` in map.yaml. Each source lists *candidate* rasters (LidarBC
tiles, STAC COG items) in its own order of preference. A gap mask on the
working grid tracks which pixels still lack data; a candidate is only fetched
if it overlaps a significant gap. Each fetched raster is clipped and
resampled onto the working grid and cached, so the terrain stage can
composite clips pixel for pixel.

Cache layout:
  .cache/sources/<source>/<key>/index.json      tile index / STAC search result
  .cache/terrain/raw/<source>/<file>            whole downloaded tiles (+ .json)
  .cache/terrain/clips/<grid>/<source>/<n>.tif  clips on the working grid (+ .json)
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..cache import CacheEntry, download, http_session, sha256_file, stable_hash, utc_now
from ..config import Bbox, Config, Source, crs_name, transform_bbox
from ..grid import Grid, terrain_grid

log = logging.getLogger(__name__)

from ..rasterops import NODATA, gaussian_smooth

# Heights below this are corrupt (e.g. nodata averaged into real values by a
# source's overviews) and are treated as nodata. Canada's lowest land is ~0 m.
MIN_VALID_ELEVATION = -1000.0
DOWNLOAD_WORKERS = 4
GTIFF_OPTS = ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=IF_SAFER"]
VSICURL_CONFIG = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.vrt",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "VSI_CACHE": "TRUE",
}


@dataclass
class Candidate:
    source_id: str
    name: str          # unique and filesystem-safe within the source
    url: str
    method: str        # "download" (whole file) or "window" (remote COG read)
    footprint: Bbox    # map CRS
    vintage: str       # survey year / item date, for reports
    group: str         # candidates in one group are fetched together
    resample: str
    smooth_sigma_m: float = 0.0   # pre-smoothing for coarse/quantised sources
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Candidate listing
# --------------------------------------------------------------------------

def _epsg_code(crs: str) -> int:
    m = re.fullmatch(r"EPSG:(\d+)", crs.strip(), re.I)
    if not m:
        raise ValueError(f"map CRS must be an EPSG code for index queries, got {crs}")
    return int(m.group(1))


def _cached_index(cfg: Config, src: Source, request: dict, refresh: bool, query) -> dict:
    entry = CacheEntry(cfg.cache_dir, src.id, request)
    if entry.is_complete() and not refresh:
        return json.loads((entry.dir / "index.json").read_text(encoding="utf-8")) | {"_record": entry.load_record()}
    started = utc_now()
    result, urls = query()
    staging = entry.staging()
    (staging / "index.json").write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    rec = entry.commit(staging, ["index.json"], {
        "title": src.title, "licence": src.licence, "retrieved_at": started, "urls": urls,
    })
    return result | {"_record": rec}


def _scale_denominator(s: str) -> int:
    digits = re.sub(r"[^\d]", "", s.split(":")[-1]) if s else ""
    return int(digits) if digits else 10**9


def lidarbc_candidates(cfg: Config, src: Source, grid: Grid, refresh: bool) -> tuple[list[Candidate], dict]:
    endpoint = src.get("endpoint").rstrip("/")
    layers = {int(k): str(v) for k, v in (src.get("index_layers") or {}).items()}
    max_year = src.get("max_year")
    epsg = _epsg_code(cfg.crs)
    request = {"kind": "lidarbc-index", "endpoint": endpoint, "layers": sorted(layers),
               "bbox": list(grid.bbox), "crs": cfg.crs, "max_year": max_year}

    def query():
        session = http_session()
        out, urls = {}, []
        for layer in sorted(layers):
            feats, offset = [], 0
            while True:
                params = {
                    "where": f"year <= {int(max_year)}" if max_year else "1=1",
                    "geometry": ",".join(str(v) for v in grid.bbox),
                    "geometryType": "esriGeometryEnvelope", "inSR": epsg, "outSR": epsg,
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "OBJECTID,filename,maptile,year,s3Url,grid_scale,oper_name,projection,spacing",
                    "returnGeometry": "true", "orderByFields": "OBJECTID",
                    "resultOffset": offset, "resultRecordCount": 1000, "f": "json",
                }
                r = session.get(f"{endpoint}/{layer}/query", params=params, timeout=120)
                r.raise_for_status()
                urls.append(r.url)
                d = r.json()
                if "error" in d:
                    raise RuntimeError(f"LidarBC index query failed: {d['error']}")
                feats += d.get("features", [])
                if not d.get("exceededTransferLimit"):
                    break
                offset += len(d.get("features", []))
            out[str(layer)] = feats
        return {"layers": out}, urls

    index = _cached_index(cfg, src, request, refresh, query)
    resample = src.get("resample", "average")
    cands = []
    for layer, feats in index["layers"].items():
        for f in feats:
            a = f["attributes"]
            pts = [p for ring in f["geometry"]["rings"] for p in ring]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            name = Path(a["filename"]).stem
            cands.append(Candidate(
                source_id=src.id, name=name, url=a["s3Url"], method="download",
                footprint=(min(xs), min(ys), max(xs), max(ys)),
                vintage=str(a["year"]), group=f"{a['year']} {a['grid_scale']}", resample=resample,
                meta={"maptile": a["maptile"], "grid_scale": a["grid_scale"], "operation": a.get("oper_name"),
                      "index_layer": layer, "filename": a["filename"]},
            ))
    # Newest survey first; within a year, the finer (newer-program) grid first.
    cands.sort(key=lambda c: (-int(c.vintage), _scale_denominator(c.meta["grid_scale"]), c.name))
    return cands, index["_record"]


def stac_candidates(cfg: Config, src: Source, grid: Grid, refresh: bool) -> tuple[list[Candidate], dict]:
    endpoint = src.get("endpoint").rstrip("/")
    collection, asset = src.get("collection"), src.get("asset")
    bbox_ll = [round(v, 5) for v in transform_bbox(grid.bbox, cfg.crs, "EPSG:4326")]
    request = {"kind": "stac-search", "endpoint": endpoint, "collection": collection, "bbox": bbox_ll}

    def query():
        session = http_session()
        r = session.get(f"{endpoint}/search", params={
            "collections": collection, "bbox": ",".join(str(v) for v in bbox_ll), "limit": 100,
        }, timeout=120)
        r.raise_for_status()
        return r.json(), [r.url]

    index = _cached_index(cfg, src, request, refresh, query)
    cands = []
    for item in index.get("features", []):
        if asset not in item.get("assets", {}):
            log.warning("  %s: item %s has no '%s' asset", src.id, item["id"], asset)
            continue
        props = item.get("properties", {})
        date = (props.get("datetime") or props.get("start_datetime") or "")[:10]
        cands.append(Candidate(
            source_id=src.id, name=re.sub(r"[^\w.-]", "_", item["id"]), url=item["assets"][asset]["href"],
            method="window", footprint=transform_bbox(tuple(item["bbox"][:4]), "EPSG:4326", cfg.crs),
            vintage=date, group=f"{collection} {date}", resample=src.get("resample", "bilinear"),
            smooth_sigma_m=float(src.get("smooth_sigma_m") or 0),
            meta={"item": item["id"], "collection": collection, "asset": asset},
        ))
    cands.sort(key=lambda c: c.vintage, reverse=True)
    return cands, index["_record"]


LISTERS = {"lidarbc": lidarbc_candidates, "stac_cog": stac_candidates}


# --------------------------------------------------------------------------
# Raw download and clipping
# --------------------------------------------------------------------------

def _raw_path(cfg: Config, c: Candidate) -> Path:
    return cfg.cache_dir / "terrain" / "raw" / c.source_id / Path(c.url).name


def ensure_raw(cfg: Config, c: Candidate, verify: bool) -> dict:
    """Download a whole tile once; return its record (url, sha256, ...)."""
    path = _raw_path(cfg, c)
    meta_path = path.with_name(path.name + ".json")
    if path.exists() and meta_path.exists():
        rec = json.loads(meta_path.read_text(encoding="utf-8"))
        if rec["bytes"] == path.stat().st_size and (not verify or sha256_file(path) == rec["sha256"]):
            return rec
        log.warning("  %s: cached tile failed verification, re-downloading", path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    url = download(http_session(), c.url, path, timeout=600)
    rec = {"url": url, "retrieved_at": started, "bytes": path.stat().st_size, "sha256": sha256_file(path),
           "path": str(path.relative_to(cfg.cache_dir)).replace("\\", "/")}
    meta_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    log.info("  downloaded %s (%.0f MB)", path.name, rec["bytes"] / 1e6)
    return rec


def _source_srs(path: str, cfg: Config) -> str | None:
    """LidarBC tiles carry compound/bound CRSs (UTM + CGVD2013). When the
    horizontal CRS is the map CRS, declare it explicitly so GDAL does no
    datum round trip and leaves heights untouched."""
    from osgeo import gdal

    ds = gdal.Open(path)
    wkt = ds.GetProjectionRef() if ds else ""
    return cfg.crs if crs_name(cfg.crs) in wkt else None


def ensure_clip(cfg: Config, grid: Grid, c: Candidate, extent: Bbox, raw: dict | None) -> dict | None:
    """Resample a candidate onto the working grid over `extent` (cached)."""
    from osgeo import gdal

    gdal.UseExceptions()
    extent = grid.snap(extent)
    if extent[2] <= extent[0] or extent[3] <= extent[1]:
        return None
    # Downloaded tiles are resampled from full resolution: some LidarBC tiles
    # ship overviews built without honouring nodata, which leaks -32767 into
    # real heights. Remote COGs use overviews to avoid pulling full detail.
    overviews = "NONE" if c.method == "download" else "AUTO"
    key = stable_hash({"url": c.url, "sha": raw and raw["sha256"], "extent": extent, "resample": c.resample,
                       "ovr": overviews, "min": MIN_VALID_ELEVATION, "smooth": c.smooth_sigma_m})[:10]
    out = cfg.cache_dir / "terrain" / "clips" / grid.key / c.source_id / f"{c.name}.{key}.tif"
    meta_path = out.with_suffix(".json")
    if out.exists() and meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))

    out.parent.mkdir(parents=True, exist_ok=True)
    if c.method == "download":
        src_path = str(cfg.cache_dir / raw["path"])
        src_srs = _source_srs(src_path, cfg)
    else:
        src_path, src_srs = "/vsicurl/" + c.url, None
    tmp = out.with_name(out.name + ".tmp.tif")
    prev = {k: gdal.GetConfigOption(k) for k in VSICURL_CONFIG}
    for k, v in VSICURL_CONFIG.items():
        gdal.SetConfigOption(k, v)
    try:
        gdal.Warp(str(tmp), src_path, options=gdal.WarpOptions(
            format="GTiff", outputBounds=extent, xRes=grid.res, yRes=grid.res,
            dstSRS=cfg.crs, srcSRS=src_srs, resampleAlg=c.resample,
            dstNodata=NODATA, outputType=gdal.GDT_Float32, creationOptions=GTIFF_OPTS,
            multithread=True, warpMemoryLimit=512 * 1024 * 1024,
            overviewLevel=overviews,
        ))
    finally:
        for k, v in prev.items():
            gdal.SetConfigOption(k, v)
    ds = gdal.Open(str(tmp), gdal.GA_Update)
    band = ds.GetRasterBand(1)
    data = band.ReadAsArray()
    bad = np.isfinite(data) & (data != NODATA) & (data < MIN_VALID_ELEVATION)
    if bad.any():
        log.warning("    %s: %d implausible heights set to nodata", c.name, int(bad.sum()))
        data[bad] = NODATA
    rejected = int(bad.sum())
    if c.smooth_sigma_m > 0:
        data = gaussian_smooth(data, c.smooth_sigma_m / grid.res)
    if rejected or c.smooth_sigma_m > 0:
        band.WriteArray(data)
    ds = None
    tmp.replace(out)
    rec = {
        "source_id": c.source_id, "name": c.name, "url": c.url, "method": c.method,
        "vintage": c.vintage, "extent": list(extent), "resample": c.resample,
        "path": str(out.relative_to(cfg.cache_dir)).replace("\\", "/"),
        "bytes": out.stat().st_size, "sha256": sha256_file(out), "created_at": utc_now(),
        "raw_sha256": raw and raw["sha256"], "meta": c.meta, "overviews": overviews,
        "smooth_sigma_m": c.smooth_sigma_m,
        "rejected_pixels": rejected,
    }
    meta_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def read_valid(cfg: Config, grid: Grid, clip: dict) -> tuple[tuple[int, int, int, int], np.ndarray]:
    from osgeo import gdal

    ds = gdal.Open(str(cfg.cache_dir / clip["path"]))
    data = ds.GetRasterBand(1).ReadAsArray()
    return grid.window(tuple(clip["extent"])), np.isfinite(data) & (data != NODATA) & (data >= MIN_VALID_ELEVATION)


# --------------------------------------------------------------------------
# Gap tracking
# --------------------------------------------------------------------------

def significant_gaps(gap: np.ndarray, min_pixels: int) -> np.ndarray:
    """Gap mask with connected gap regions smaller than min_pixels removed."""
    from scipy import ndimage

    if min_pixels <= 1 or not gap.any():
        return gap
    labels, n = ndimage.label(gap)
    if n == 0:
        return gap
    sizes = np.bincount(labels.ravel())
    keep = sizes >= min_pixels
    keep[0] = False
    return keep[labels]


def gap_bbox(grid: Grid, mask: np.ndarray, footprint: Bbox) -> Bbox | None:
    """Bbox (map CRS) of the gap pixels inside a footprint, or None."""
    fp = grid.snap(footprint)
    if fp[2] <= fp[0] or fp[3] <= fp[1]:
        return None
    col, row, w, h = grid.window(fp)
    sub = mask[row:row + h, col:col + w]
    if not sub.any():
        return None
    rows = np.flatnonzero(sub.any(axis=1))
    cols = np.flatnonzero(sub.any(axis=0))
    r = grid.res
    x0, y1 = grid.bbox[0] + (col + cols[0]) * r, grid.bbox[3] - (row + rows[0]) * r
    x1, y0 = grid.bbox[0] + (col + cols[-1] + 1) * r, grid.bbox[3] - (row + rows[-1] + 1) * r
    return (x0, y0, x1, y1)


# --------------------------------------------------------------------------
# Coordinator
# --------------------------------------------------------------------------

def _groups(cands: list[Candidate]):
    group: list[Candidate] = []
    for c in cands:
        if group and c.group != group[0].group:
            yield group
            group = []
        group.append(c)
    if group:
        yield group


def parse_priority(entries: list[str]) -> list[tuple[str, set[str] | None]]:
    """Parse terrain.sources entries: "source" or "source:2022" / "source:2016,2017".
    The year filter restricts which vintages that entry contributes."""
    out = []
    for e in entries:
        sid, _, years = str(e).partition(":")
        out.append((sid.strip(), {y.strip() for y in years.split(",") if y.strip()} or None))
    return out


def fetch_terrain(cfg: Config, entries: list[str], refresh: bool = False, verify: bool = False) -> tuple[dict, dict]:
    """Fetch terrain sources in priority order. Returns (records per source, selection).

    `entries` are terrain.sources items; the same source may appear several
    times with different year filters, interleaved with other sources.
    """
    tcfg = cfg.map["terrain"]
    grid = terrain_grid(cfg)
    min_px = max(1, int(round(float(tcfg.get("min_fill_area_m2", 0)) / grid.res ** 2)))
    gap = np.ones((grid.height, grid.width), dtype=bool)
    total = gap.size
    log.info("terrain grid %s: %d x %d px at %g m, gaps under %d px are interpolated",
             grid.key, grid.width, grid.height, grid.res, min_px)

    listed: dict[str, tuple[list[Candidate], dict]] = {}
    used: dict[str, list[dict]] = {}
    raws: dict[str, dict[str, dict]] = {}
    selection: list[dict] = []
    for sid, years in parse_priority(entries):
        src = cfg.sources[sid]
        label = sid + (":" + ",".join(sorted(years)) if years else "")
        sig = significant_gaps(gap, min_px)
        if not sig.any():
            log.info("terrain fully covered; %s not needed", label)
            break
        if sid not in listed:
            listed[sid] = LISTERS[src.kind](cfg, src, grid, refresh)
        cands = [c for c in listed[sid][0] if years is None or c.vintage[:4] in years]
        log.info("%s: %d candidate rasters", label, len(cands))
        src_used = used.setdefault(sid, [])
        src_raws = raws.setdefault(sid, {})
        for group in _groups(cands):
            todo = [(c, gap_bbox(grid, sig, c.footprint)) for c in group]
            todo = [(c, b) for c, b in todo if b is not None]
            if not todo:
                continue
            log.info("  %s: %d of %d rasters overlap remaining gaps", group[0].group, len(todo), len(group))
            dl = [c for c, _ in todo if c.method == "download"]
            with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
                for c, rec in zip(dl, pool.map(lambda c: ensure_raw(cfg, c, verify), dl)):
                    src_raws[c.name] = rec
            for c, gb in todo:
                # Downloaded tiles are clipped to their whole footprint (cheap,
                # local); remote COGs only over the gap they are filling.
                extent = c.footprint if c.method == "download" else gb
                clip = ensure_clip(cfg, grid, c, extent, src_raws.get(c.name))
                if clip is None:
                    continue
                (col, row, w, h), valid = read_valid(cfg, grid, clip)
                win = gap[row:row + h, col:col + w]
                filled = int((win & valid).sum())
                win &= ~valid
                if filled == 0:
                    log.info("    %s: no new coverage", c.name)
                    continue
                log.info("    %s (%s): +%.2f%% of grid", c.name, c.vintage, 100 * filled / total)
                entry = {**clip, "pixels_filled": filled, "priority_entry": label}
                src_used.append(entry)
                selection.append(entry)
            sig = significant_gaps(gap, min_px)
            if not sig.any():
                break

    records: dict[str, dict] = {}
    for sid, (cands, index_rec) in listed.items():
        src, u = cfg.sources[sid], used.get(sid, [])
        # Only tiles that contributed pixels are inputs to this build.
        r = {k: v for k, v in raws.get(sid, {}).items() if k in {x["name"] for x in u}}
        if not u:
            continue  # listed but contributed nothing: not part of this build
        files = [{"path": x["path"], "bytes": x["bytes"], "sha256": x["sha256"]} for x in r.values()]
        files += [{"path": x["path"], "bytes": x["bytes"], "sha256": x["sha256"]} for x in u if x["method"] == "window"]
        records[sid] = {
            "source_id": sid, "title": src.title, "licence": src.licence,
            "request": index_rec["request"], "request_key": index_rec["request_key"],
            "retrieved_at": index_rec["retrieved_at"],
            "urls": index_rec["urls"] + sorted({x["url"] for x in r.values()} | {x["url"] for x in u if x["method"] == "window"}),
            "files": sorted(files, key=lambda f: f["path"]),
            "feature_count": len(u),
            "candidates": len(cands),
        }

    remaining = int(gap.sum())
    sel = {
        "grid": {"key": grid.key, "crs": grid.crs, "bbox": list(grid.bbox), "res": grid.res,
                 "width": grid.width, "height": grid.height},
        "clips": selection,
        "remaining_gap_pixels": remaining,
        "remaining_gap_percent": round(100 * remaining / total, 4),
        "significant_gap_pixels": int(significant_gaps(gap, min_px).sum()),
    }
    log.info("terrain coverage: %.3f%% of grid without data after all sources", sel["remaining_gap_percent"])
    return records, sel
