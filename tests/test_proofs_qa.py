from types import SimpleNamespace

import pytest
from osgeo import ogr, osr

from pipeline import config
from pipeline.pdftile import PT_PER_MM, plan
from pipeline.stages import qa


def test_plan_covers_sheet_with_overlap():
    w, h = 24 * 72, 36 * 72                       # 24 x 36 in, in points
    p = plan(w, h, "letter", margin_mm=8, overlap_mm=12)
    assert p.rows * p.cols == len(p.tiles)
    xs = sorted({t.x0 for t in p.tiles})
    ys = sorted({t.y0 for t in p.tiles})
    t0 = p.tiles[0]
    assert xs[0] == 0 and xs[-1] + t0.w == pytest.approx(w)          # left and right edges covered
    assert ys[0] == pytest.approx(0) and ys[-1] + t0.h == pytest.approx(h)
    if p.cols > 1:
        assert t0.w - (xs[1] - xs[0]) >= 12 * PT_PER_MM - 1e-6          # at least the requested overlap
    # windows are exactly the printable area (100% scale)
    assert t0.w == pytest.approx(min(p.page_w, p.page_h) - 2 * 8 * PT_PER_MM) or \
           t0.w == pytest.approx(max(p.page_w, p.page_h) - 2 * 8 * PT_PER_MM)


def test_plan_picks_fewer_pages():
    p_letter = plan(24 * 72, 36 * 72, "letter", 8, 12)
    p_legal = plan(24 * 72, 36 * 72, "legal", 8, 12)
    assert len(p_legal.tiles) <= len(p_letter.tiles)


def _gpkg(path, feats):
    srs = osr.SpatialReference(); srs.ImportFromEPSG(2955)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
    lyr = ds.CreateLayer("trails", srs, ogr.wkbMultiLineString)
    for f in ("source_id", "class", "name", "difficulty"):
        lyr.CreateField(ogr.FieldDefn(f, ogr.OFTString))
    for sid, name, diff, coords in feats:
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("source_id", sid); f.SetField("class", "path"); f.SetField("name", name); f.SetField("difficulty", diff)
        f.SetGeometry(ogr.CreateGeometryFromWkt("MULTILINESTRING((" + ",".join(f"{x} {y}" for x, y in coords) + "))"))
        lyr.CreateFeature(f)
    ds = None


def test_compare_reports_changes(repo, tmp_path):
    cfg = config.load(repo)
    x, y = cfg.sides[0].extent[0] + 1000, cfg.sides[0].extent[1] + 1000
    line = [(x, y), (x + 1000, y)]
    _gpkg(tmp_path / "old.gpkg", [("osm:way/1", "A", "easy", line), ("osm:way/2", "B", "easy", line),
                                  ("osm:way/3", "C", "easy", line), ("osm:way/4", "D", "easy", line)])
    _gpkg(tmp_path / "new.gpkg", [("osm:way/1", "A", "easy", line), ("osm:way/2", "B", "difficult", line),
                                  ("osm:way/3", "C2", "easy", line), ("osm:way/4", "D", "easy", [(x, y), (x + 2000, y)]),
                                  ("osm:way/5", "E", "easy", line)])
    cfg.map["qa"] = {"change_layers": ["trails"], "length_change_threshold": 0.1}
    rows = {(r["source_id"], r["change"]) for r in qa.compare(cfg, tmp_path / "new.gpkg", tmp_path / "old.gpkg")}
    assert rows == {("osm:way/2", "reclassified"), ("osm:way/3", "renamed"),
                    ("osm:way/4", "reshaped"), ("osm:way/5", "added")}


def test_grid_ref_and_link(repo):
    cfg = config.load(repo)
    s = cfg.sides[1]
    assert qa.grid_ref(cfg, s.extent[0] + 10, s.extent[1] + 10).endswith("(side B)")
    assert qa.link("osm:way/42") == "[osm:way/42](https://www.openstreetmap.org/way/42)"
    assert qa.link("cgn:ABC") == "`cgn:ABC`"
