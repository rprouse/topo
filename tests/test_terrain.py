from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pipeline.grid import Grid, snap_outward
from pipeline.rasterops import gaussian_smooth
from pipeline.sources.terrain import NODATA, gap_bbox, parse_priority, significant_gaps
from pipeline.stages import terrain as T

CRS = "EPSG:2955"


def _grid(w=100, h=80, res=2.0):
    return Grid(CRS, (600000.0, 5480000.0, 600000.0 + w * res, 5480000.0 + h * res), res)


def _cfg(tmp_path: Path, grid: Grid, **terrain):
    t = {"contour_interval_m": 20, "index_every": 5, "contour_simplify_m": 0.5,
         "contour_smooth_iterations": 2, "contour_min_length_m": 0, "fill_max_distance_m": 10,
         "spot_heights_max": 10, "spot_height_radius_m": 40, "spot_height_min_relief_m": 20, **terrain}
    return SimpleNamespace(cache_dir=tmp_path, crs=CRS, aoi=grid.bbox, map={"terrain": t}, sides=[None])


# ---- grid -----------------------------------------------------------------

def test_snap_outward():
    assert snap_outward((1.5, 2.1, 9.9, 10.0), 2) == (0, 2, 10, 10)


def test_grid_snap_and_window():
    g = _grid()
    b = g.snap((600003.1, 5480010.5, 600011.0, 5480020.0))
    assert b == (600002.0, 5480010.0, 600012.0, 5480020.0)
    assert g.window(b) == (1, 70, 5, 5)
    assert g.snap((0, 0, 1, 1))[2] <= g.snap((0, 0, 1, 1))[0]  # outside -> empty


# ---- gap tracking ---------------------------------------------------------

def test_significant_gaps_drops_small_holes():
    gap = np.zeros((50, 50), bool)
    gap[2:4, 2:4] = True          # 4 px hole
    gap[20:40, 20:40] = True      # 400 px region
    sig = significant_gaps(gap, 100)
    assert not sig[2:4, 2:4].any() and sig[20:40, 20:40].all()


def test_gap_bbox():
    g = _grid()
    mask = np.zeros((g.height, g.width), bool)
    mask[10:20, 30:40] = True
    b = gap_bbox(g, mask, g.bbox)
    assert b == (600060.0, 5480120.0, 600080.0, 5480140.0)
    assert gap_bbox(g, mask, (600000.0, 5480000.0, 600010.0, 5480010.0)) is None


# ---- composite priority ---------------------------------------------------

def _clip(tmp_path, grid, name, value, extent, holes=()):
    sub = Grid(CRS, extent, grid.res)
    data = np.full((sub.height, sub.width), value, np.float32)
    for r, c in holes:
        data[r, c] = NODATA
    path = tmp_path / f"{name}.tif"
    T.write_raster(path, sub, data, NODATA, T.GTIFF_F32)
    return {"path": path.name, "extent": list(extent), "source_id": name, "vintage": "2024", "name": name}


def test_composite_first_clip_wins_and_fills_gaps(tmp_path):
    g = _grid()
    high = _clip(tmp_path, g, "high", 100.0, (600000.0, 5480000.0, 600100.0, 5480160.0), holes=[(5, 5)])
    low = _clip(tmp_path, g, "low", 50.0, g.bbox)
    cfg = _cfg(tmp_path, g, fill_max_distance_m=0)
    T.composite(cfg, g, [high, low], tmp_path / "dem.tif", tmp_path / "src.tif")
    dem, src = T.read_raster(tmp_path / "dem.tif"), T.read_raster(tmp_path / "src.tif")
    assert dem[0, 0] == 100 and src[0, 0] == 1
    assert dem[5, 5] == 50 and src[5, 5] == 2       # hole in high filled by low
    assert dem[0, 99] == 50 and src[0, 99] == 2     # outside high's extent


def test_composite_interpolates_small_voids(tmp_path):
    g = _grid()
    only = _clip(tmp_path, g, "only", 10.0, g.bbox, holes=[(40, 50), (40, 51)])
    T.composite(_cfg(tmp_path, g), g, [only], tmp_path / "dem.tif", tmp_path / "src.tif")
    dem, src = T.read_raster(tmp_path / "dem.tif"), T.read_raster(tmp_path / "src.tif")
    assert dem[40, 50] == pytest.approx(10.0) and src[40, 50] == T.SRC_INTERPOLATED


# ---- smoothing / lines ----------------------------------------------------

def test_gaussian_smooth_respects_nodata():
    dem = np.full((60, 60), 100.0, np.float32)
    dem[:, :30] = NODATA
    out = gaussian_smooth(dem, 3.0, strip=16)
    assert (out[:, :30] == NODATA).all()
    assert np.allclose(out[:, 30:], 100.0, atol=1e-3)   # no bleed from nodata


def test_gaussian_smooth_strips_match_whole():
    rng = np.random.default_rng(1)
    dem = rng.normal(1000, 20, (300, 200)).astype(np.float32)
    assert np.allclose(gaussian_smooth(dem, 2.0, strip=37), gaussian_smooth(dem, 2.0, strip=1000), atol=1e-3)


def test_chaikin_keeps_open_endpoints_and_closes_rings():
    line = np.array([[0, 0], [10, 0], [10, 10]], float)
    out = T.chaikin(line, closed=False, iterations=2)
    assert (out[0] == line[0]).all() and (out[-1] == line[-1]).all() and len(out) > len(line)
    ring = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], float)
    out = T.chaikin(ring, closed=True, iterations=1)
    assert (out[0] == out[-1]).all() and len(out) == 9


def test_is_index():
    assert T.is_index(1500, 20, 5) and not T.is_index(1520, 20, 5)
    assert T.is_index(1550, 10, 5) and not T.is_index(1540, 10, 5)


# ---- end to end on a synthetic hill --------------------------------------

def _hill(g: Grid, peak=1295.0, base=1000.0):
    yy, xx = np.mgrid[0:g.height, 0:g.width]
    cy, cx = g.height / 2, g.width / 2
    r2 = ((yy - cy) ** 2 + (xx - cx) ** 2) * g.res ** 2
    return (base + (peak - base) * np.exp(-r2 / (2 * 50.0 ** 2))).astype(np.float32)


@pytest.mark.parametrize("interval", [20, 10])
def test_contours_and_spot_heights_on_hill(tmp_path, interval):
    from osgeo import ogr

    g = _grid(150, 150)
    cfg = _cfg(tmp_path, g, contour_interval_m=interval)
    T.write_raster(tmp_path / "dem.tif", g, _hill(g), NODATA, T.GTIFF_F32)
    T.write_raster(tmp_path / "smooth.tif", g, _hill(g), NODATA, T.GTIFF_F32)

    res = T.contours(cfg, g, tmp_path / "smooth.tif", tmp_path / "c.gpkg")
    ds = ogr.Open(str(tmp_path / "c.gpkg"))
    lyr = ds.GetLayer("contours")
    levels = sorted({f.GetField("elev") for f in lyr})
    assert res["contours"] == len(levels)            # one closed ring per level
    assert levels[0] > 1000 and levels[-1] < 1300
    assert all(l % interval == 0 for l in levels)
    lyr.ResetReading()
    idx = {f.GetField("elev") for f in lyr if f.GetField("index") == 1}
    assert idx == {l for l in levels if l % (interval * 5) == 0}

    T.spot_heights(cfg, g, tmp_path / "dem.tif", tmp_path / "smooth.tif", tmp_path / "s.gpkg")
    sds = ogr.Open(str(tmp_path / "s.gpkg"))
    feats = list(sds.GetLayer(0))
    assert len(feats) == 1
    p = feats[0].GetGeometryRef()
    cx, cy = (g.bbox[0] + g.bbox[2]) / 2, (g.bbox[1] + g.bbox[3]) / 2
    assert abs(p.GetX() - cx) <= 2 * g.res and abs(p.GetY() - cy) <= 2 * g.res
    assert feats[0].GetField("elev") == pytest.approx(1295, abs=1)


def test_parse_priority():
    assert parse_priority(["lidarbc_dem:2024", "hrdem", "lidarbc_dem:2016,2017"]) == [
        ("lidarbc_dem", {"2024"}), ("hrdem", None), ("lidarbc_dem", {"2016", "2017"})]
