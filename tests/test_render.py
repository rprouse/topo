import datetime as dt

import pytest
import yaml

from pipeline import config, declination
from pipeline.layout import LayoutError, sheet_geometry
from pipeline.stages.render import collar_texts


def _edit_map(root, fn):
    p = root / "config" / "map.yaml"
    m = yaml.safe_load(p.read_text())
    fn(m)
    p.write_text(yaml.safe_dump(m))


def test_sheet_geometry_exact_scale(repo):
    cfg = config.load(repo)
    g = sheet_geometry(cfg, cfg.sides[0])
    assert g.page_w == pytest.approx(24 * 25.4) and g.page_h == pytest.approx(36 * 25.4)
    assert g.map.w == pytest.approx(14000 / 25000 * 1000)   # 560 mm
    assert g.map.h == pytest.approx(21000 / 25000 * 1000)   # 840 mm
    assert g.title.bottom == pytest.approx(g.map.y) and g.collar.y == pytest.approx(g.map.bottom)
    assert g.collar.bottom <= g.page_h and g.title.y >= 0


def test_sheet_geometry_at_30000(repo):
    _edit_map(repo, lambda m: m.update(scale=30000))
    cfg = config.load(repo)
    g = sheet_geometry(cfg, cfg.sides[0])
    assert g.map.w == pytest.approx(14000 / 30000 * 1000)
    assert g.map.x == pytest.approx((g.page_w - g.map.w) / 2)


def test_sheet_geometry_reports_misfit(repo):
    _edit_map(repo, lambda m: m["sheet"]["grid"].update(face_height_m=30000))
    cfg = config.load(repo)
    with pytest.raises(LayoutError, match="sheet A"):
        sheet_geometry(cfg, cfg.sides[0])


def test_neighbours_ignore_diagonals(repo):
    from pipeline.layout import neighbours

    cfg = config.load(repo)
    by = {s.id: s for s in cfg.sides}
    assert sorted(neighbours(cfg, by["E"])) == [("B", "north"), ("D", "west"), ("F", "east"), ("H", "south")]
    assert sorted(neighbours(cfg, by["A"])) == [("B", "east"), ("D", "south")]


def test_overview_scale(repo):
    from pipeline.layout import overview_geometry

    cfg = config.load(repo)
    scale, g = overview_geometry(cfg)
    assert scale == 75_000                       # 40 x 61 km on a 24 x 36 in sheet
    assert g.map.h == pytest.approx(61000 / 75000 * 1000)


def test_declination_matches_noaa():
    # NOAA calculator, WMMHR-2025, 49.50389 N 115.06278 W, sea level, 2026.8904: 13.7383 deg
    d = declination.declination(49.50389, -115.06278, dt.date(2026, 11, 22))
    assert d == pytest.approx(13.7383, abs=0.002)


def test_convergence_sign_and_size(repo):
    cfg = config.load(repo)
    x, y = (cfg.aoi[0] + cfg.aoi[2]) / 2, (cfg.aoi[1] + cfg.aoi[3]) / 2
    c = declination.convergence(cfg, x, y)
    # East of the zone 11 central meridian (117 W), grid north is east of true north:
    # (117 - 115.06) * sin(49.5) ~ 1.47 deg.
    assert c == pytest.approx(1.47, abs=0.05)


def test_collar_texts(repo):
    cfg = config.load(repo)
    north = declination.compute(cfg)
    t = collar_texts(cfg, ["osm", "fwa_streams", "hrdem"], north, "2026-11-01")
    assert "1:25,000" in t["scale_note"] and "Contour interval 20 m" in t["scale_note"]
    assert "Magnetic declination" in t["scale_note"] and "° E" in t["scale_note"]
    assert t["attribution"].startswith("Map data © OpenStreetMap contributors")
    assert "Open Government Licence - Canada" in t["attribution"]
    assert "PLACEHOLDER" in t["disclaimer"]
    assert "Edition 2027.1" in t["edition"] and "2026-11-01" in t["edition"]
