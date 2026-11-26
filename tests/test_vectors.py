import json
from pathlib import Path

import pytest
from osgeo import ogr

from pipeline import corrections as corr
from pipeline import osmmap
from pipeline.corrections import Feature
from pipeline.stages import vectors as V

REPO = Path(__file__).resolve().parent.parent


def pt(x, y):
    g = ogr.Geometry(ogr.wkbPoint)
    g.AddPoint_2D(x, y)
    return g


def line(*xy):
    g = ogr.Geometry(ogr.wkbLineString)
    for x, y in xy:
        g.AddPoint_2D(x, y)
    return g


# ---- tag mapping ---------------------------------------------------------

def test_parse_hstore():
    assert osmmap.parse_hstore('"sac_scale"=>"hiking","name:en"=>"A \\"quoted\\" trail"') == {
        "sac_scale": "hiking", "name:en": 'A "quoted" trail'}
    assert osmmap.parse_hstore(None) == {}


@pytest.mark.parametrize("cond,tags,ok", [
    ({"highway": "path"}, {"highway": "path"}, True),
    ({"highway": ["path", "footway"]}, {"highway": "footway"}, True),
    ({"surface": False}, {"highway": "residential"}, True),
    ({"surface": False}, {"surface": "gravel"}, False),
    ({"building": True}, {"building": "yes"}, True),
    ({"name": {"regex": r"(?i)\bfsr\b"}}, {"name": "Coal Creek FSR"}, True),
    ({"name": {"regex": r"(?i)\bfsr\b"}}, {}, False),
    ({"surface": {"not": ["gravel"]}}, {}, True),
    ({"surface": {"not": ["gravel"]}}, {"surface": "gravel"}, False),
])
def test_matches(cond, tags, ok):
    assert osmmap.matches(cond, tags) is ok


@pytest.fixture(scope="module")
def mapping():
    return osmmap.load(REPO / "config" / "osm_mapping.yaml")


@pytest.mark.parametrize("tags,cls", [
    ({"highway": "trunk"}, "highway"),
    ({"highway": "unclassified", "name": "Lizard Creek FSR"}, "fsr"),
    ({"highway": "residential"}, "paved"),
    ({"highway": "unclassified"}, "gravel"),
    ({"highway": "unclassified", "surface": "asphalt"}, "paved"),
    ({"highway": "track", "tracktype": "grade2"}, "track"),
    ({"highway": "track"}, "track_4wd"),
])
def test_road_classes(mapping, tags, cls):
    assert mapping["roads"].classify(tags)["class"] == cls


def test_trail_attributes(mapping):
    r = mapping["trails"].classify({"highway": "path", "sac_scale": "mountain_hiking", "mtb:scale": "2", "name": "Hyme"})
    assert r["class"] == "path" and r["sac_scale"] == "mountain_hiking" and r["mtb_scale"] == "2" and r["name"] == "Hyme"
    assert "difficulty" not in r          # trails are not graded
    assert mapping["trails"].classify({"highway": "trunk"}) is None


def test_class_from_tag(mapping):
    assert mapping["lifts"].classify({"aerialway": "chair_lift"})["class"] == "chair_lift"


def test_mapping_validation(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("layers:\n  x:\n    from: [lines]\n    geometry: line\n    rules:\n      - {when: {a: b}}\n")
    with pytest.raises(osmmap.MappingError, match="class"):
        osmmap.load(p)
    p.write_text("layers:\n  x:\n    from: [multipolygons]\n    geometry: point\n    rules:\n      - {class: c}\n")
    with pytest.raises(osmmap.MappingError, match="as_point"):
        osmmap.load(p)


# ---- clipping ------------------------------------------------------------

def test_clip_line_and_point():
    box = V.bbox_polygon((0, 0, 10, 10))
    g = V.clip(line((-5, 5), (15, 5)), "line", box)
    assert g.GetGeometryName() == "MULTILINESTRING" and g.Length() == pytest.approx(10)
    assert V.clip(line((20, 20), (30, 30)), "line", box) is None
    assert V.clip(pt(5, 5), "point", box) is not None and V.clip(pt(11, 5), "point", box) is None


def test_parse_number():
    assert V.parse_number("2788") == 2788 and V.parse_number("2788 m") == 2788
    assert V.parse_number("1000 ft") == pytest.approx(304.8) and V.parse_number("n/a") is None


# ---- names ---------------------------------------------------------------

def _peak(name, x, y, sid):
    return Feature("pois", pt(x, y), {"source": "osm", "source_id": sid, "class": "peak", "name": name})


def _official(name, x, y, sid, concise="MTN"):
    return Feature("names", pt(x, y), {"source": "cgn", "source_id": sid, "class": concise, "name": name,
                                       "concise": concise, "matched": 0})


def test_reconcile_names_prefers_same_name_and_keeps_osm_name():
    feats = [
        _peak("Planner Peak", 0, 0, "osm:node/1"),        # 6 m from the official "Three Sisters" point
        _peak("Three Sisters", 1500, 0, "osm:node/2"),    # same name, 1.5 km away
        _peak(None, 5000, 0, "osm:node/3"),
        _official("Three Sisters", 6, 0, "cgn:A"),
        _official("Lizard Range", 5300, 0, "cgn:B"),
        _official("Hosmer Ridge", 50000, 0, "cgn:C"),
    ]
    rows = V.reconcile_names(feats, {"peak": {"concise": ["MTN"], "radius_m": 750, "name_radius_m": 5000}})
    by = {f.source_id: f.props for f in feats}
    assert by["osm:node/2"]["name_status"] == "match" and by["osm:node/2"]["official_id"] == "cgn:A"
    assert by["osm:node/1"]["name"] == "Planner Peak" and by["osm:node/1"]["name_status"] == "osm_only"
    assert by["osm:node/3"]["name"] is None and by["osm:node/3"]["name_status"] == "unnamed_near_official"
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["official_only", "osm_only", "unnamed_near_official"]


def test_normalise_name():
    assert V.normalise_name("Mt. Fernie") == V.normalise_name("Mount  Fernie")


# ---- corrections ---------------------------------------------------------

KINDS = {"trails": "line", "pois": "point"}
FIELDS = {"trails": ["class", "name", "surface"], "pois": ["class", "name", "ele"]}


def _trail(sid, name="T"):
    return Feature("trails", line((0, 0), (1, 1)), {"source": "osm", "source_id": sid, "class": "path",
                                                   "name": name, "surface": "ground", "hidden": 0, "closed": 0})


def test_corrections_apply():
    feats = [_trail("osm:way/1"), _trail("osm:way/2"), _trail("osm:way/3"), _trail("osm:way/4")]
    edits = [
        {"id": "e1", "source_id": "osm:way/1", "action": "set", "field": "surface", "value": "gravel"},
        {"id": "e2", "source_id": "osm:way/2", "action": "hide"},
        {"id": "e3", "source_id": "osm:way/3", "action": "delete"},
        {"id": "e4", "source_id": "osm:way/4", "action": "close", "value": "Bridge out 2026"},
        {"id": "e5", "source_id": "osm:way/999", "action": "hide"},
        {"id": "e6", "source_id": "osm:way/1", "action": "set", "field": "nope", "value": "x"},
        {"id": "e7", "source_id": "corr:a1", "action": "set", "field": "name", "value": "Renamed"},
    ]
    adds = [{"id": "a1", "target_layer": "pois", "class": "hut", "name": "Island Lake Hut",
             "attributes": json.dumps({"ele": 1400}), "kind": "point", "geom": pt(5, 5), "from_layer": "added_points"},
            {"id": "a2", "target_layer": "trails", "kind": "point", "geom": pt(1, 1), "from_layer": "added_points"}]
    out, rep = corr.apply(feats, KINDS, FIELDS, edits, adds)
    by = {f.source_id: f for f in out}
    assert by["osm:way/1"].props["surface"] == "gravel"
    assert by["osm:way/2"].props["hidden"] == 1
    assert "osm:way/3" not in by
    assert by["osm:way/4"].props["closed"] == 1 and by["osm:way/4"].props["closure_note"] == "Bridge out 2026"
    assert by["corr:a1"].props["name"] == "Renamed" and by["corr:a1"].props["ele"] == 1400
    assert [s["id"] for s in rep["stale"]] == ["e5"]
    errs = {e["id"] for e in rep["errors"]}
    assert errs == {"e6", "a2"}   # unknown field; point added to a line layer


def test_corrections_template_roundtrip(tmp_path):
    p = corr.init(tmp_path / "c.gpkg", "EPSG:2955")
    with pytest.raises(FileExistsError):
        corr.init(p, "EPSG:2955")
    ds = ogr.Open(str(p), 1)
    lyr = ds.GetLayerByName("edits")
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("id", "e1"); f.SetField("source_id", "osm:way/1"); f.SetField("action", "hide")
    lyr.CreateFeature(f)
    lyr = ds.GetLayerByName("added_points")
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetField("id", "a1"); f.SetField("target_layer", "pois"); f.SetGeometry(pt(1, 2))
    lyr.CreateFeature(f)
    ds = None
    edits, adds = corr.load(p)
    assert edits[0]["source_id"] == "osm:way/1" and adds[0]["kind"] == "point" and adds[0]["geom"].GetX() == 1
