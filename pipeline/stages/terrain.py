"""Stage 2: terrain products from the clips selected by the fetch stage.

Outputs in build/terrain/:
  dem.tif                   composite DEM on the working grid (small voids interpolated)
  dem_source.tif            which source supplied each pixel (see terrain-report.json)
  dem_smooth.tif            Gaussian-smoothed DEM, used only for contours and summits
  contours.gpkg             layer `contours`: elev, index (1 = index contour)
  hillshade.tif             hillshade from the unsmoothed DEM, print resolution
  spot_heights.gpkg         layer `spot_height_candidates` (filtered in milestone 3)
  terrain-report.json       coverage by source, voids, elevation range, counts
  coverage.png              colour map of dem_source.tif
  preview.png               hillshade + contours rendered through QGIS

Each step records the hash of its inputs, so changing e.g. the contour
interval re-runs contours and the preview but not the composite.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

from .. import state
from ..cache import stable_hash
from ..config import Config
from ..grid import Grid, terrain_grid
from ..rasterops import gaussian_smooth
from ..sources.terrain import MIN_VALID_ELEVATION, NODATA

log = logging.getLogger(__name__)

GTIFF_F32 = ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3", "BIGTIFF=IF_SAFER"]
GTIFF_BYTE = ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"]
SRC_INTERPOLATED = 254   # dem_source value for interpolated (filled) pixels
STEP_VERSION = 1         # bump to force every step to re-run after code changes

# Colours for coverage.png, by source id (others get a fallback palette).
SOURCE_COLOURS = {
    "lidarbc_dem": [(27, 120, 55), (90, 174, 97), (166, 219, 160), (217, 240, 211)],
    "hrdem": [(33, 102, 172), (103, 169, 207)],
    "cdem": [(230, 171, 2)],
}


# --------------------------------------------------------------------------
# Raster helpers
# --------------------------------------------------------------------------

def _gdal():
    from osgeo import gdal

    gdal.UseExceptions()
    return gdal


def write_raster(path: Path, grid: Grid, data: np.ndarray, nodata: float | None, opts: list[str],
                 res: float | None = None) -> None:
    gdal = _gdal()
    from osgeo import osr

    dtype = {np.dtype("float32"): gdal.GDT_Float32, np.dtype("uint8"): gdal.GDT_Byte}[data.dtype]
    tmp = path.with_name(path.name + ".tmp.tif")
    ds = gdal.GetDriverByName("GTiff").Create(str(tmp), data.shape[1], data.shape[0], 1, dtype, opts)
    r = res or grid.res
    ds.SetGeoTransform((grid.bbox[0], r, 0, grid.bbox[3], 0, -r))
    srs = osr.SpatialReference()
    srs.SetFromUserInput(grid.crs)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.WriteArray(data)
    ds = None
    tmp.replace(path)


def read_raster(path: Path) -> np.ndarray:
    ds = _gdal().Open(str(path))  # keep the dataset alive while reading its band
    return ds.GetRasterBand(1).ReadAsArray()


def add_overviews(path: Path, resampling: str = "AVERAGE") -> None:
    gdal = _gdal()
    ds = gdal.Open(str(path), gdal.GA_Update)
    gdal.SetConfigOption("COMPRESS_OVERVIEW", "DEFLATE")
    levels = [f for f in (2, 4, 8, 16, 32, 64) if min(ds.RasterXSize, ds.RasterYSize) // f >= 256]
    if levels:
        ds.BuildOverviews(resampling, levels)
    ds = None


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

def composite(cfg: Config, grid: Grid, clips: list[dict], out_dem: Path, out_src: Path) -> dict:
    """Stack clips in priority order (first wins), then interpolate small voids."""
    gdal = _gdal()
    dem = np.full((grid.height, grid.width), NODATA, dtype=np.float32)
    src = np.zeros((grid.height, grid.width), dtype=np.uint8)
    if len(clips) >= SRC_INTERPOLATED:
        raise RuntimeError("too many terrain clips for a byte source raster")
    for i, clip in enumerate(clips, start=1):
        data = read_raster(cfg.cache_dir / clip["path"])
        col, row, w, h = grid.window(tuple(clip["extent"]))
        take = ((src[row:row + h, col:col + w] == 0) & np.isfinite(data) & (data != NODATA)
                & (data >= MIN_VALID_ELEVATION))
        dem[row:row + h, col:col + w][take] = data[take]
        src[row:row + h, col:col + w][take] = i
    write_raster(out_dem, grid, dem, NODATA, GTIFF_F32)

    max_dist = float(cfg.map["terrain"].get("fill_max_distance_m", 0)) / grid.res
    if max_dist > 0 and (src == 0).any():
        ds = gdal.Open(str(out_dem), gdal.GA_Update)
        gdal.FillNodata(ds.GetRasterBand(1), None, maxSearchDist=max_dist, smoothingIterations=0)
        ds = None
        filled = read_raster(out_dem)
        src[(src == 0) & (filled != NODATA)] = SRC_INTERPOLATED
    write_raster(out_src, grid, src, 0, GTIFF_BYTE)
    add_overviews(out_dem)
    return {"clips": len(clips)}


def chaikin(coords: np.ndarray, closed: bool, iterations: int) -> np.ndarray:
    """Chaikin corner cutting. Open lines keep their end points."""
    pts = coords
    for _ in range(iterations):
        if len(pts) < 3:
            break
        if closed:
            ring = pts[:-1] if np.allclose(pts[0], pts[-1]) else pts
            nxt = np.roll(ring, -1, axis=0)
            q = 0.75 * ring + 0.25 * nxt
            r = 0.25 * ring + 0.75 * nxt
            new = np.empty((2 * len(ring), 2))
            new[0::2], new[1::2] = q, r
            pts = np.vstack([new, new[:1]])
        else:
            a, b = pts[:-1], pts[1:]
            q = 0.75 * a + 0.25 * b
            r = 0.25 * a + 0.75 * b
            mid = np.empty((2 * len(a), 2))
            mid[0::2], mid[1::2] = q, r
            pts = np.vstack([pts[:1], mid[1:-1], pts[-1:]])
    return pts


def is_index(elev: float, interval: float, index_every: int) -> bool:
    step = interval * index_every
    return abs(elev / step - round(elev / step)) < 1e-6


def contours(cfg: Config, grid: Grid, smooth_path: Path, out: Path) -> dict:
    gdal = _gdal()
    from osgeo import ogr, osr

    t = cfg.map["terrain"]
    interval = float(t["contour_interval_m"])
    index_every = int(t["index_every"])
    tol = float(t.get("contour_simplify_m", 0))
    iters = int(t.get("contour_smooth_iterations", 0))
    min_len = float(t.get("contour_min_length_m", 0))

    srs = osr.SpatialReference()
    srs.SetFromUserInput(cfg.crs)
    mem = ogr.GetDriverByName("Memory").CreateDataSource("contours")
    raw = mem.CreateLayer("raw", srs, ogr.wkbLineString)
    raw.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
    raw.CreateField(ogr.FieldDefn("elev", ogr.OFTReal))
    src_ds = gdal.Open(str(smooth_path))
    gdal.ContourGenerateEx(src_ds.GetRasterBand(1), raw, options=[
        # ID_FIELD / ELEV_FIELD are field indexes, not names.
        f"LEVEL_INTERVAL={interval}", "ID_FIELD=0", "ELEV_FIELD=1", f"NODATA={NODATA}",
    ])

    if out.exists():
        out.unlink()
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(out))
    lyr = ds.CreateLayer("contours", srs, ogr.wkbLineString, options=["SPATIAL_INDEX=YES"])
    lyr.CreateField(ogr.FieldDefn("elev", ogr.OFTReal))
    lyr.CreateField(ogr.FieldDefn("index", ogr.OFTInteger))
    defn = lyr.GetLayerDefn()
    kept = dropped = vertices = 0
    lyr.StartTransaction()
    for f in raw:
        g = f.GetGeometryRef()
        if g is None or g.GetPointCount() < 2:
            continue
        closed = g.GetPoint_2D(0) == g.GetPoint_2D(g.GetPointCount() - 1)
        if closed and g.Length() < min_len:
            dropped += 1
            continue
        if tol > 0:
            g = g.Simplify(tol)
        pts = np.array(g.GetPoints())[:, :2]
        pts = chaikin(pts, closed, iters)
        line = ogr.Geometry(ogr.wkbLineString)
        for x, y in pts:
            line.AddPoint_2D(float(x), float(y))
        if tol > 0:
            line = line.Simplify(tol / 2)
        elev = f.GetField("elev")
        nf = ogr.Feature(defn)
        nf.SetField("elev", round(elev, 3))
        nf.SetField("index", int(is_index(elev, interval, index_every)))
        nf.SetGeometry(line)
        lyr.CreateFeature(nf)
        kept += 1
        vertices += line.GetPointCount()
    lyr.CommitTransaction()
    ds = None
    return {"contours": kept, "dropped_short_closed": dropped, "vertices": vertices,
            "interval_m": interval, "index_every": index_every}


def hillshade(cfg: Config, grid: Grid, dem_path: Path, out: Path) -> dict:
    gdal = _gdal()
    t = cfg.map["terrain"]
    res = float(t.get("hillshade_resolution_m", grid.res))
    src = str(dem_path)
    if abs(res - grid.res) > 1e-9:
        src = str(out.with_name("dem_hillshade_res.vrt"))
        gdal.Warp(src, str(dem_path), options=gdal.WarpOptions(
            format="VRT", xRes=res, yRes=res, outputBounds=grid.bbox,
            resampleAlg="average" if res > grid.res else "bilinear"))
    multi = bool(t.get("multidirectional", False))
    kw = dict(zFactor=float(t.get("hillshade_z_factor", 1.0)), altitude=float(t.get("hillshade_altitude", 45)),
              computeEdges=True, multiDirectional=multi, creationOptions=GTIFF_BYTE)
    if not multi:
        kw["azimuth"] = float(t.get("hillshade_azimuth", 315))
    tmp = out.with_name(out.name + ".tmp.tif")
    gdal.DEMProcessing(str(tmp), src, "hillshade", options=gdal.DEMProcessingOptions(**kw))
    tmp.replace(out)
    add_overviews(out)
    return {"resolution_m": res, "multidirectional": multi}


def spot_heights(cfg: Config, grid: Grid, dem_path: Path, smooth_path: Path, out: Path) -> dict:
    from osgeo import ogr, osr
    from scipy import ndimage

    t = cfg.map["terrain"]
    radius = float(t.get("spot_height_radius_m", 400))
    min_relief = float(t.get("spot_height_min_relief_m", 50))
    cap = int(t.get("spot_heights_max", 40)) * 3   # vectors stage filters down to the cap

    k = max(1, round(10 / grid.res))                 # detect on a ~10 m grid
    sm = read_raster(smooth_path)
    h, w = (sm.shape[0] // k) * k, (sm.shape[1] // k) * k
    blocks = sm[:h, :w].reshape(h // k, k, w // k, k)
    valid = (blocks != NODATA).all(axis=(1, 3))
    coarse = np.where(valid, blocks.mean(axis=(1, 3)), np.nan)

    size = 2 * int(math.ceil(radius / (grid.res * k))) + 1
    hi = np.where(np.isfinite(coarse), coarse, -np.inf)
    lo = np.where(np.isfinite(coarse), coarse, np.inf)
    mx = ndimage.maximum_filter(hi, size=size, mode="constant", cval=np.inf)  # edges never qualify
    mn = ndimage.minimum_filter(lo, size=size, mode="nearest")
    peaks = np.isfinite(coarse) & (hi >= mx) & (coarse - mn >= min_relief)
    labels, n = ndimage.label(peaks)                  # plateaus -> one candidate
    centres = ndimage.labeled_comprehension(np.arange(peaks.size).reshape(peaks.shape), labels,
                                            np.arange(1, n + 1), np.min, np.int64, -1) if n else []

    dem = read_raster(dem_path)
    ax0, ay0, ax1, ay1 = cfg.aoi
    found = []
    for flat in centres:
        r, c = divmod(int(flat), coarse.shape[1])
        win = dem[r * k:(r + 1) * k, c * k:(c + 1) * k]
        if (win == NODATA).all():
            continue
        rr, cc = np.unravel_index(np.argmax(np.where(win == NODATA, -np.inf, win)), win.shape)
        x = grid.bbox[0] + (c * k + cc + 0.5) * grid.res
        y = grid.bbox[3] - (r * k + rr + 0.5) * grid.res
        if not (ax0 <= x <= ax1 and ay0 <= y <= ay1):
            continue
        found.append((float(coarse[r, c] - mn[r, c]), float(win[rr, cc]), x, y))
    found.sort(key=lambda p: (-p[0], -p[1]))
    found = found[:cap]

    srs = osr.SpatialReference()
    srs.SetFromUserInput(cfg.crs)
    if out.exists():
        out.unlink()
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(out))
    lyr = ds.CreateLayer("spot_height_candidates", srs, ogr.wkbPoint)
    for name, typ in (("elev", ogr.OFTInteger), ("relief", ogr.OFTInteger), ("rank", ogr.OFTInteger)):
        lyr.CreateField(ogr.FieldDefn(name, typ))
    for i, (relief, elev, x, y) in enumerate(found, start=1):
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("elev", int(round(elev)))
        f.SetField("relief", int(round(relief)))
        f.SetField("rank", i)
        p = ogr.Geometry(ogr.wkbPoint)
        p.AddPoint_2D(x, y)
        f.SetGeometry(p)
        lyr.CreateFeature(f)
    ds = None
    return {"candidates": len(found)}


def _palette(clips: list[dict]) -> dict[int, tuple[int, int, int]]:
    """Colour per dem_source value: a hue per source, a shade per vintage."""
    pal = {0: (255, 255, 255), SRC_INTERPOLATED: (215, 48, 39)}
    fallback = [(117, 112, 179), (231, 41, 138), (102, 102, 102)]
    for i, c in enumerate(clips, start=1):
        shades = SOURCE_COLOURS.get(c["source_id"], fallback)
        vintages = sorted({x["vintage"] for x in clips if x["source_id"] == c["source_id"]}, reverse=True)
        pal[i] = shades[min(vintages.index(c["vintage"]), len(shades) - 1)]
    return pal


def coverage(cfg: Config, grid: Grid, clips: list[dict], src_path: Path, dem_path: Path,
             png: Path) -> dict:
    gdal = _gdal()
    src = read_raster(src_path)
    col, row, w, h = grid.window(grid.snap(cfg.aoi))
    aoi = src[row:row + h, col:col + w]

    def pct(a, cond):
        return round(100.0 * float(np.count_nonzero(cond)) / a.size, 3)

    by_source: dict[str, dict] = {}
    for i, c in enumerate(clips, start=1):
        key = f"{c['source_id']} {c['vintage']}"
        e = by_source.setdefault(key, {"source_id": c["source_id"], "vintage": c["vintage"],
                                       "aoi_percent": 0.0, "grid_percent": 0.0, "rasters": []})
        e["aoi_percent"] += pct(aoi, aoi == i)
        e["grid_percent"] += pct(src, src == i)
        e["rasters"].append(c["name"])
    for e in by_source.values():
        e["aoi_percent"] = round(e["aoi_percent"], 3)
        e["grid_percent"] = round(e["grid_percent"], 3)

    dem = read_raster(dem_path)
    valid = dem != NODATA
    report = {
        "by_source": list(by_source.values()),
        "interpolated_aoi_percent": pct(aoi, aoi == SRC_INTERPOLATED),
        "no_data_aoi_percent": pct(aoi, aoi == 0),
        "no_data_grid_percent": pct(src, src == 0),
        "elevation_min_m": round(float(dem[valid].min()), 1) if valid.any() else None,
        "elevation_max_m": round(float(dem[valid].max()), 1) if valid.any() else None,
        "source_values": {str(i): {"source_id": c["source_id"], "vintage": c["vintage"], "name": c["name"]}
                          for i, c in enumerate(clips, start=1)} | {str(SRC_INTERPOLATED): "interpolated", "0": "no data"},
    }

    # Colour PNG, about 1500 px wide.
    pal = _palette(clips)
    mem = gdal.GetDriverByName("MEM").Create("", src.shape[1], src.shape[0], 1, gdal.GDT_Byte)
    mem.GetRasterBand(1).WriteArray(src)
    ct = gdal.ColorTable()
    for v, rgb in pal.items():
        ct.SetColorEntry(v, (*rgb, 255))
    mem.GetRasterBand(1).SetRasterColorTable(ct)
    scale = max(1, src.shape[1] // 1500)
    gdal.Translate(str(png), mem, options=gdal.TranslateOptions(
        format="PNG", width=src.shape[1] // scale, height=src.shape[0] // scale, resampleAlg="nearest"))
    report["coverage_png_colours"] = {str(k): "#%02x%02x%02x" % v for k, v in pal.items()}
    return report


def preview(cfg: Config, out_dir: Path) -> None:
    """Render in a subprocess: PyQGIS can crash on teardown, and that must not
    take the pipeline down with it."""
    subprocess.run([sys.executable, "-m", "pipeline.preview", "--root", str(cfg.root),
                    "--terrain-dir", str(out_dir)], check=True)


# --------------------------------------------------------------------------
# Stage
# --------------------------------------------------------------------------

def run(cfg: Config, force: bool = False) -> dict:
    fetch_state = state.read(cfg.build_dir, "fetch") or {}
    sel = fetch_state.get("terrain")
    if not sel or not sel.get("clips"):
        raise SystemExit("No terrain has been fetched. Run `make fetch` first.")
    grid = terrain_grid(cfg)
    if sel["grid"]["key"] != grid.key:
        raise SystemExit("The terrain grid changed (AOI, CRS or dem_resolution_m) since the last fetch. "
                         "Run `make fetch` again.")
    clips = sel["clips"]
    t = cfg.map["terrain"]
    out = cfg.build_dir / "terrain"
    out.mkdir(parents=True, exist_ok=True)
    p = {n: out / n for n in ("dem.tif", "dem_source.tif", "dem_smooth.tif", "contours.gpkg",
                              "hillshade.tif", "spot_heights.gpkg", "coverage.png", "preview.png")}

    prev = state.read(cfg.build_dir, "terrain") or {}
    prev_steps = {} if force else prev.get("steps", {})
    steps: dict[str, str] = {}
    results: dict[str, dict] = dict(prev.get("results", {}))

    def step(name: str, deps: dict, outputs: list[Path], fn):
        h = stable_hash({"v": STEP_VERSION, **deps})
        steps[name] = h
        if prev_steps.get(name) == h and all(o.exists() for o in outputs):
            log.info("terrain: %-12s up to date", name)
            return
        log.info("terrain: %-12s running", name)
        r = fn()
        if r is not None:
            results[name] = r

    clip_key = [(c["path"], c["sha256"]) for c in clips]
    step("composite", {"clips": clip_key, "grid": grid.key, "fill": t.get("fill_max_distance_m")},
         [p["dem.tif"], p["dem_source.tif"]],
         lambda: composite(cfg, grid, clips, p["dem.tif"], p["dem_source.tif"]))
    step("smooth", {"up": steps["composite"], "sigma": t.get("smoothing_sigma_m")}, [p["dem_smooth.tif"]],
         lambda: (write_raster(p["dem_smooth.tif"], grid,
                               gaussian_smooth(read_raster(p["dem.tif"]), float(t.get("smoothing_sigma_m", 0)) / grid.res),
                               NODATA, GTIFF_F32), {"sigma_m": t.get("smoothing_sigma_m")})[1])
    step("contours", {"up": steps["smooth"], **{k: t.get(k) for k in (
        "contour_interval_m", "index_every", "contour_simplify_m", "contour_smooth_iterations", "contour_min_length_m")}},
         [p["contours.gpkg"]], lambda: contours(cfg, grid, p["dem_smooth.tif"], p["contours.gpkg"]))
    step("hillshade", {"up": steps["composite"], **{k: t.get(k) for k in (
        "hillshade_resolution_m", "hillshade_azimuth", "hillshade_altitude", "hillshade_z_factor", "multidirectional")}},
         [p["hillshade.tif"]], lambda: hillshade(cfg, grid, p["dem.tif"], p["hillshade.tif"]))
    step("spot_heights", {"up": steps["smooth"], "aoi": cfg.aoi, **{k: t.get(k) for k in (
        "spot_heights_max", "spot_height_radius_m", "spot_height_min_relief_m")}},
         [p["spot_heights.gpkg"]], lambda: spot_heights(cfg, grid, p["dem.tif"], p["dem_smooth.tif"], p["spot_heights.gpkg"]))
    step("coverage", {"up": steps["composite"], "aoi": cfg.aoi}, [p["coverage.png"]],
         lambda: coverage(cfg, grid, clips, p["dem_source.tif"], p["dem.tif"], p["coverage.png"]))
    step("preview", {"up": [steps[s] for s in ("contours", "hillshade", "spot_heights")], "aoi": cfg.aoi,
                     "scale": cfg.scale, "dpi": t.get("preview_dpi")}, [p["preview.png"]],
         lambda: preview(cfg, out))

    report = {
        "grid": sel["grid"],
        "fetch_remaining_gap_percent": sel.get("remaining_gap_percent"),
        **results.get("coverage", {}),
        "contours": results.get("contours"),
        "hillshade": results.get("hillshade"),
        "spot_heights": results.get("spot_heights"),
    }
    (out / "terrain-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    state.write(cfg.build_dir, "terrain", stable_hash(steps),
                {"steps": steps, "results": results,
                 "outputs": sorted(str(x.relative_to(cfg.root)).replace("\\", "/") for x in p.values())})
    for e in report.get("by_source", []):
        log.info("  %-12s %-6s %6.2f%% of AOI", e["source_id"], e["vintage"], e["aoi_percent"])
    log.info("  interpolated %.3f%%, no data %.3f%% of AOI; %s contours; elevation %s-%s m",
             report.get("interpolated_aoi_percent", 0), report.get("no_data_aoi_percent", 0),
             (report.get("contours") or {}).get("contours"), report.get("elevation_min_m"), report.get("elevation_max_m"))
    return report
