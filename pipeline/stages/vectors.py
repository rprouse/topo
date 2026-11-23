"""Stage 3: normalise every vector source into build/map.gpkg.

Sources: OSM (through config/osm_mapping.yaml), the BC Freshwater Atlas
(water), Canadian Geographical Names (official names) and the terrain
stage's spot-height candidates. All are reprojected to the map CRS and
clipped to the buffered AOI. The corrections layer is applied last.

Every feature carries: source, source_id (stable, e.g. osm:way/123),
class, name, hidden, closed, closure_note, plus its layer's own fields.
See docs/schema.md.

Also writes build/vectors-report.json (counts, corrections applied, stale
corrections, OSM vs official name comparison) and keeps the previous map as
build/map.previous.gpkg for the QA change report.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

from .. import corrections as corr
from .. import osmmap, state
from ..cache import sha256_file, stable_hash
from ..config import Config
from ..corrections import Feature

log = logging.getLogger(__name__)

STEP_VERSION = 2
COMMON = ["source", "source_id", "class", "name"]
TAIL = ["hidden", "closed", "closure_note"]
NAME_FIELDS = ["name_osm", "name_official", "official_id", "name_status"]

# BC Freshwater Atlas stream EDGE_TYPEs that are real watercourses (FWA User
# Guide, table 2). Everything else in the network is a construction line
# (connectors through lakes, double-line river centrelines, inferred links).
FWA_DRAWN_EDGES = {1000: "stream", 1050: "stream", 1100: "stream", 1150: "stream",
                   2000: "canal", 2300: "canal"}
FWA_INTERMITTENT = "GA24850150"
FWA_AREAS = {"fwa_lakes": "lake", "fwa_rivers": "river", "fwa_wetlands": "wetland"}

# Layers not driven by the OSM mapping: geometry kind and own fields.
FIXED_LAYERS = {
    "water_lines": ("line", ["stream_order", "intermittent"]),
    "water_areas": ("polygon", ["waterbody_type"]),
    "names": ("point", ["concise", "generic", "status", "decision", "location", "matched"]),
    "spot_heights": ("point", ["elev", "relief", "rank"]),
}
FIELD_TYPES = {"stream_order": "int", "intermittent": "int", "matched": "int", "elev": "real",
               "relief": "int", "rank": "int", "ele": "real", "hidden": "int", "closed": "int"}


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------

def _ogr():
    from osgeo import gdal, ogr

    gdal.UseExceptions()
    return ogr


def transformer(src_crs, dst: str):
    from osgeo import osr

    def srs(x):
        if isinstance(x, osr.SpatialReference):
            s = x.Clone()
        else:
            s = osr.SpatialReference()
            s.SetFromUserInput(x)
        s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        return s

    return osr.CoordinateTransformation(srs(src_crs), srs(dst))


def bbox_polygon(b):
    ogr = _ogr()
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3]), (b[0], b[1])):
        ring.AddPoint_2D(x, y)
    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    return poly


def _parts(g, want: int) -> list:
    """Flatten a geometry into single parts of the wanted flat type."""
    ogr = _ogr()
    t = ogr.GT_Flatten(g.GetGeometryType())
    if t == want:
        return [g]
    if t in (ogr.wkbGeometryCollection, ogr.wkbMultiPolygon, ogr.wkbMultiLineString, ogr.wkbMultiPoint):
        out = []
        for i in range(g.GetGeometryCount()):
            out += _parts(g.GetGeometryRef(i), want)
        return out
    return []


def clip(g, kind: str, clip_poly, as_point: bool = False):
    """Clip to the AOI and normalise to Point / MultiLineString / MultiPolygon.
    Returns None if nothing is left."""
    ogr = _ogr()
    if g is None or g.IsEmpty():
        return None
    g = g.Clone()
    g.FlattenTo2D()
    if as_point and ogr.GT_Flatten(g.GetGeometryType()) in (ogr.wkbPolygon, ogr.wkbMultiPolygon):
        if not g.IsValid():
            g = g.MakeValid()
        g = g.PointOnSurface()
    if kind == "point":
        if ogr.GT_Flatten(g.GetGeometryType()) != ogr.wkbPoint or not clip_poly.Contains(g):
            return None
        return g
    if kind == "polygon" and not g.IsValid():
        g = g.MakeValid()
    env = g.GetEnvelope()  # (minx, maxx, miny, maxy)
    cenv = clip_poly.GetEnvelope()
    if env[1] < cenv[0] or env[0] > cenv[1] or env[3] < cenv[2] or env[2] > cenv[3]:
        return None
    inside = env[0] >= cenv[0] and env[1] <= cenv[1] and env[2] >= cenv[2] and env[3] <= cenv[3]
    if not inside:
        g = g.Intersection(clip_poly)
        if g is None or g.IsEmpty():
            return None
    want = ogr.wkbLineString if kind == "line" else ogr.wkbPolygon
    parts = _parts(g, want)
    if not parts:
        return None
    multi = ogr.Geometry(ogr.wkbMultiLineString if kind == "line" else ogr.wkbMultiPolygon)
    for p in parts:
        multi.AddGeometry(p)
    return multi


def parse_number(v) -> float | None:
    if v is None:
        return None
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(v).replace(",", "."))
    if not m:
        return None
    n = float(m.group(1))
    return n * 0.3048 if re.search(r"(ft|')\s*$", str(v)) else n


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

def _record_file(cfg: Config, rec: dict, name: str | None = None) -> Path:
    f = next(x for x in rec["files"] if name is None or x["path"] == name)
    return cfg.cache_dir / "sources" / rec["source_id"] / rec["request_key"] / f["path"]


def read_osm(path: Path, mappings: dict[str, osmmap.LayerMapping], crs: str, clip_poly) -> list[Feature]:
    from osgeo import gdal

    gdal.UseExceptions()
    ct = transformer("EPSG:4326", crs)
    feats: list[Feature] = []
    by_src: dict[str, list[osmmap.LayerMapping]] = {}
    for m in mappings.values():
        for s in m.sources:
            by_src.setdefault(s, []).append(m)
    for src_layer, maps in by_src.items():
        ds = gdal.OpenEx(str(path), gdal.OF_VECTOR, allowed_drivers=["OSM"])
        lyr = ds.GetLayerByName(src_layer)
        defn = lyr.GetLayerDefn()
        names = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
        for f in lyr:
            tags = {n: str(f.GetField(n)) for n in names if n not in osmmap.NON_TAG_FIELDS and f.IsFieldSetAndNotNull(n)}
            tags.update(osmmap.parse_hstore(f.GetField("other_tags") if "other_tags" in names else None))
            hits = [(m, r) for m in maps if (r := m.classify(tags)) is not None]
            if not hits:
                continue
            if src_layer == "points":
                sid = f"osm:node/{f.GetField('osm_id')}"
            elif src_layer == "lines":
                sid = f"osm:way/{f.GetField('osm_id')}"
            else:
                rel = f.GetField("osm_id")
                sid = f"osm:relation/{rel}" if rel else f"osm:way/{f.GetField('osm_way_id')}"
            g = f.GetGeometryRef()
            if g is None:
                continue
            g = g.Clone()
            g.Transform(ct)
            for m, r in hits:
                cg = clip(g, m.geometry, clip_poly, m.as_point)
                if cg is None:
                    continue
                if "ele" in r:
                    r["ele"] = parse_number(r["ele"])
                feats.append(Feature(m.name, cg, {"source": "osm", "source_id": sid, **r}))
        ds = None
    return feats


def read_geojson(path: Path):
    """Yield (properties, OGR geometry, source SRS) from a GeoJSON file."""
    ogr = _ogr()
    ds = ogr.Open(str(path))
    lyr = ds.GetLayer(0)
    srs = lyr.GetSpatialRef()
    for f in lyr:
        g = f.GetGeometryRef()
        yield f.items(), (g.Clone() if g else None), srs
    ds = None


def read_fwa(cfg: Config, recs: dict[str, dict], clip_poly) -> list[Feature]:
    feats: list[Feature] = []
    if "fwa_streams" in recs:
        ct = None
        for p, g, srs in read_geojson(_record_file(cfg, recs["fwa_streams"])):
            cls = FWA_DRAWN_EDGES.get(p.get("EDGE_TYPE"))
            if cls is None or g is None:
                continue
            ct = ct or transformer(srs, cfg.crs)
            g.Transform(ct)
            cg = clip(g, "line", clip_poly)
            if cg is None:
                continue
            inter = int(p.get("FEATURE_CODE") == FWA_INTERMITTENT)
            feats.append(Feature("water_lines", cg, {
                "source": "fwa", "source_id": f"fwa:stream/{p['LINEAR_FEATURE_ID']}",
                "class": "intermittent" if inter and cls == "stream" else cls,
                "name": p.get("GNIS_NAME"), "stream_order": p.get("STREAM_ORDER"), "intermittent": inter}))
    for sid, cls in FWA_AREAS.items():
        if sid not in recs:
            continue
        ct = None
        for p, g, srs in read_geojson(_record_file(cfg, recs[sid])):
            if g is None:
                continue
            ct = ct or transformer(srs, cfg.crs)
            g.Transform(ct)
            cg = clip(g, "polygon", clip_poly)
            if cg is None:
                continue
            feats.append(Feature("water_areas", cg, {
                "source": "fwa", "source_id": f"fwa:{cls}/{p['WATERBODY_POLY_ID']}", "class": cls,
                "name": p.get("GNIS_NAME_1"), "waterbody_type": p.get("WATERBODY_TYPE")}))
    return feats


def read_cgn(cfg: Config, rec: dict, clip_poly) -> list[Feature]:
    ogr = _ogr()
    ct = transformer("EPSG:4326", cfg.crs)
    items = json.loads(_record_file(cfg, rec).read_text(encoding="utf-8"))
    feats = []
    for it in items:
        if it.get("longitude") is None:
            continue
        p = ogr.Geometry(ogr.wkbPoint)
        p.AddPoint_2D(it["longitude"], it["latitude"])
        p.Transform(ct)
        if not clip_poly.Contains(p):
            continue
        code = lambda k: (it.get(k) or {}).get("code") if isinstance(it.get(k), dict) else it.get(k)
        feats.append(Feature("names", p, {
            "source": "cgn", "source_id": f"cgn:{it['id']}", "class": code("concise"), "name": it.get("name"),
            "concise": code("concise"), "generic": code("generic"), "status": code("status"),
            "decision": it.get("decision"), "location": it.get("location"), "matched": 0}))
    return feats


# --------------------------------------------------------------------------
# Official names and spot heights
# --------------------------------------------------------------------------

def normalise_name(n: str | None) -> str:
    if not n:
        return ""
    n = n.casefold().replace("’", "'")
    n = re.sub(r"\bmt\.?\s", "mount ", n)
    return re.sub(r"\s+", " ", n).strip()


def reconcile_names(features: list[Feature], rules: dict[str, dict]) -> list[dict]:
    """Compare OSM peaks/places with official names (CGN) of the right kind.

    Pass 1 pairs features whose names agree (normalised) within `name_radius_m`;
    pass 2 pairs the rest by proximity within `radius_m`. Pairing is one-to-one,
    nearest first. OSM features keep their OSM name: the official name is only
    recorded (name_official, official_id, name_status), and every disagreement
    is returned for review, to be settled with a `set name` correction.
    Matching alone can't tell a summit from the ridge or range it sits on.
    """
    names = [f for f in features if f.layer == "names"]
    rows = []
    for key, rule in rules.items():
        layer, cls = {"peak": ("pois", "peak"), "place": ("places", None)}[key]
        concise = set(rule.get("concise", []))
        radius = float(rule.get("radius_m", 500))
        name_radius = float(rule.get("name_radius_m", 5000))
        osm = [f for f in features if f.layer == layer and f.props.get("source") == "osm"
               and (cls is None or f.props.get("class") == cls)]
        cand = [n for n in names if n.props.get("concise") in concise]
        used_o: set[int] = set()
        used_n: set[int] = set()

        def pair(same_name: bool, limit: float):
            pairs = []
            for o in osm:
                if id(o) in used_o:
                    continue
                for n in cand:
                    if id(n) in used_n:
                        continue
                    same = bool(o.props.get("name")) and normalise_name(o.props.get("name")) == normalise_name(n.props.get("name"))
                    if same != same_name:
                        continue
                    d = o.geom.Distance(n.geom)
                    if d <= limit:
                        pairs.append((d, o, n))
            pairs.sort(key=lambda p: p[0])
            for d, o, n in pairs:
                if id(o) in used_o or id(n) in used_n:
                    continue
                used_o.add(id(o))
                used_n.add(id(n))
                osm_name, off = o.props.get("name"), n.props.get("name")
                status = "match" if same_name else ("unnamed_near_official" if not osm_name else "differs")
                o.props.update({"name_osm": osm_name, "name_official": off, "official_id": n.source_id,
                                "name_status": status})
                n.props["matched"] = 1
                if status != "match":
                    rows.append({"kind": key, "status": status, "source_id": o.source_id, "official_id": n.source_id,
                                 "name_osm": osm_name, "name_official": off, "distance_m": round(d),
                                 "x": round(o.geom.GetX()), "y": round(o.geom.GetY())})

        pair(True, name_radius)
        pair(False, radius)
        for o in osm:
            if id(o) not in used_o:
                o.props.update({"name_osm": o.props.get("name"),
                                "name_status": "osm_only" if o.props.get("name") else None})
                if o.props.get("name"):
                    rows.append({"kind": key, "status": "osm_only", "source_id": o.source_id,
                                 "name_osm": o.props.get("name"), "x": round(o.geom.GetX()), "y": round(o.geom.GetY())})
        for n in cand:
            if id(n) not in used_n:
                rows.append({"kind": key, "status": "official_only", "official_id": n.source_id,
                             "name_official": n.props.get("name"), "x": round(n.geom.GetX()), "y": round(n.geom.GetY())})
    return rows


def spot_heights(cfg: Config, path: Path, features: list[Feature]) -> list[Feature]:
    """Summit candidates from the terrain stage, minus those near a known peak."""
    ogr = _ogr()
    vcfg = cfg.map.get("vectors", {})
    radius = float(vcfg.get("spot_height_exclude_radius_m", 400))
    cap = int(cfg.map["terrain"].get("spot_heights_max", 40))
    peak_codes = set((vcfg.get("name_match", {}).get("peak") or {}).get("concise", ["MTN"]))
    known = [f.geom for f in features if (f.layer == "pois" and f.props.get("class") == "peak")
             or (f.layer == "names" and f.props.get("concise") in peak_codes)]
    ds = ogr.Open(str(path))
    lyr = ds.GetLayer(0)
    cands = sorted(((f.GetField("rank"), f.GetField("elev"), f.GetField("relief"), f.GetGeometryRef().Clone()) for f in lyr),
                   key=lambda c: c[0])
    out = []
    for rank, elev, relief, g in cands:
        if any(g.Distance(k) <= radius for k in known):
            continue
        out.append(Feature("spot_heights", g, {
            "source": "dem", "source_id": f"dem:spot/{round(g.GetX())}_{round(g.GetY())}",
            "class": "spot_height", "name": None, "elev": elev, "relief": relief, "rank": len(out) + 1}))
        if len(out) >= cap:
            break
    return out


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def layer_schemas(mappings: dict[str, osmmap.LayerMapping]) -> dict[str, tuple[str, list[str]]]:
    """layer -> (geometry kind, ordered field list)."""
    out = {}
    for name, m in mappings.items():
        extra = [f for f in m.fields if f != "name"]
        if name in ("pois", "places"):
            extra += NAME_FIELDS
        out[name] = (m.geometry, COMMON + extra + TAIL)
    for name, (kind, extra) in FIXED_LAYERS.items():
        out[name] = (kind, COMMON + extra + TAIL)
    return out


def write_gpkg(path: Path, crs: str, schemas: dict, features: list[Feature]) -> dict[str, int]:
    ogr = _ogr()
    from osgeo import osr

    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs)
    tmp = path.with_name("map.tmp.gpkg")
    if tmp.exists():
        tmp.unlink()
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(tmp))
    gtype = {"point": ogr.wkbPoint, "line": ogr.wkbMultiLineString, "polygon": ogr.wkbMultiPolygon}
    otype = {"int": ogr.OFTInteger, "real": ogr.OFTReal}
    counts = {}
    by_layer: dict[str, list[Feature]] = {}
    for f in features:
        by_layer.setdefault(f.layer, []).append(f)
    for name, (kind, fields) in schemas.items():
        lyr = ds.CreateLayer(name, srs, gtype[kind], options=["SPATIAL_INDEX=YES", "FID=fid"])
        for fld in fields:
            lyr.CreateField(ogr.FieldDefn(fld, otype.get(FIELD_TYPES.get(fld, ""), ogr.OFTString)))
        defn = lyr.GetLayerDefn()
        lyr.StartTransaction()
        feats = sorted(by_layer.get(name, []), key=lambda f: (f.props.get("class") or "", f.source_id))
        for f in feats:
            of = ogr.Feature(defn)
            for fld in fields:
                v = f.props.get(fld, 0 if fld in ("hidden", "closed") else None)
                if v is None:
                    continue
                t = FIELD_TYPES.get(fld)
                try:
                    of.SetField(fld, int(v) if t == "int" else float(v) if t == "real" else str(v))
                except (TypeError, ValueError):
                    pass
            of.SetGeometry(f.geom)
            lyr.CreateFeature(of)
        lyr.CommitTransaction()
        counts[name] = len(feats)
    ds = None
    if path.exists():
        shutil.move(str(path), str(path.with_name("map.previous.gpkg")))
    tmp.replace(path)
    return counts


# --------------------------------------------------------------------------
# Stage
# --------------------------------------------------------------------------

def _class_counts(features: Iterable[Feature]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for f in features:
        c = out.setdefault(f.layer, {})
        k = str(f.props.get("class"))
        c[k] = c.get(k, 0) + 1
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def run(cfg: Config, force: bool = False) -> dict:
    fetch = (state.read(cfg.build_dir, "fetch") or {}).get("sources", {})
    vcfg = cfg.map.get("vectors", {})
    mapping_path = cfg.root / vcfg.get("osm_mapping", "config/osm_mapping.yaml")
    corr_path = cfg.root / vcfg.get("corrections", "authored/corrections.gpkg")
    spot_path = cfg.build_dir / "terrain" / "spot_heights.gpkg"
    if "osm" not in fetch:
        raise SystemExit("OSM has not been fetched. Run `make fetch` first.")

    used = {k: v for k, v in fetch.items() if k == "osm" or k == "cgn_names" or k.startswith("fwa_")}
    inputs = {
        "v": STEP_VERSION,
        "sources": {k: [f["sha256"] for f in v["files"]] for k, v in sorted(used.items())},
        "mapping": sha256_file(mapping_path),
        "corrections": sha256_file(corr_path) if corr_path.exists() else None,
        "spots": sha256_file(spot_path) if spot_path.exists() else None,
        "vectors": vcfg, "spot_max": cfg.map["terrain"].get("spot_heights_max"),
        "crs": cfg.crs, "aoi": cfg.aoi_buffered,
    }
    h = stable_hash(inputs)
    out = cfg.build_dir / "map.gpkg"
    report_path = cfg.build_dir / "vectors-report.json"
    if not force and state.is_current(cfg.build_dir, "vectors", h, [out, report_path]):
        log.info("vectors: up to date")
        return json.loads(report_path.read_text(encoding="utf-8"))

    mappings = osmmap.load(mapping_path)
    schemas = layer_schemas(mappings)
    clip_poly = bbox_polygon(cfg.aoi_buffered)

    log.info("vectors: OSM (snapshot %s)", fetch["osm"].get("snapshot"))
    features = read_osm(_record_file(cfg, fetch["osm"]), mappings, cfg.crs, clip_poly)
    log.info("vectors: Freshwater Atlas")
    features += read_fwa(cfg, fetch, clip_poly)
    name_rows: list[dict] = []
    if "cgn_names" in fetch:
        log.info("vectors: official names")
        features += read_cgn(cfg, fetch["cgn_names"], clip_poly)
        name_rows = reconcile_names(features, vcfg.get("name_match", {}))
    if spot_path.exists():
        features += spot_heights(cfg, spot_path, features)
    else:
        log.warning("vectors: no terrain spot-height candidates (run `make terrain`); skipping spot heights")

    edits, adds = corr.load(corr_path)
    if not corr_path.exists():
        log.warning("vectors: %s not found; no corrections applied", corr_path.relative_to(cfg.root))
    features, corr_report = corr.apply(
        features, {k: v[0] for k, v in schemas.items()},
        {k: [f for f in v[1] if f not in COMMON[:2] + TAIL] for k, v in schemas.items()}, edits, adds)
    for e in corr_report["errors"]:
        log.error("corrections: %s: %s", e["id"], e["error"])
    if corr_report["stale"]:
        log.warning("corrections: %d stale edit(s) - their source feature no longer exists", len(corr_report["stale"]))

    counts = write_gpkg(out, cfg.crs, schemas, features)
    report = {
        "osm": {k: fetch["osm"].get(k) for k in ("snapshot", "osm_base", "retrieved_at")},
        "layers": counts,
        "classes": _class_counts(features),
        "corrections": corr_report,
        "names": name_rows,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    state.write(cfg.build_dir, "vectors", h, {"layers": counts, "inputs": inputs})
    for layer, n in counts.items():
        log.info("  %-16s %6d", layer, n)
    statuses: dict[str, int] = {}
    for r in name_rows:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    log.info("  names to review: %s", statuses or "none")
    return report
