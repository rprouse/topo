"""The hand-authored corrections layer: authored/corrections.gpkg.

Applied last in the vectors stage. Layers:

  edits            no geometry. Change features by source_id:
                     id         unique id for this edit (e.g. e001)
                     source_id  feature to change, as in map.gpkg (osm:way/123, fwa:stream/456, corr:a001)
                     layer      optional: only touch the feature in this map layer
                     action     hide | delete | set | close
                     field      for set: the field to change (e.g. name, class, surface)
                     value      for set: the new value; for close: the closure note
                     note, author, edited   free text, for the record
  added_points     new point features      } fields: id, target_layer, class,
  added_lines      new line features       }   name, attributes (JSON object of
  added_polygons   new polygon features    }   other fields), note, author, edited

hide keeps the feature but sets hidden=1 (styles skip it); delete removes it;
close sets closed=1 and closure_note. Added features get source "corrections"
and source_id "corr:<id>", so edits can target them too.

Edits whose source feature no longer exists are reported as stale.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ACTIONS = ("hide", "delete", "set", "close")
EDIT_FIELDS = ("id", "source_id", "layer", "action", "field", "value", "note", "author", "edited")
ADD_FIELDS = ("id", "target_layer", "class", "name", "attributes", "note", "author", "edited")
ADD_LAYERS = {"added_points": "point", "added_lines": "line", "added_polygons": "polygon"}


@dataclass
class Feature:
    """A map feature in memory: geometry (OGR, map CRS) plus properties."""
    layer: str
    geom: Any
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return self.props["source_id"]


def init(path: Path, crs: str, force: bool = False) -> Path:
    """Create an empty corrections GeoPackage with the expected layers."""
    from osgeo import gdal, ogr, osr

    gdal.UseExceptions()
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists (use --force to replace it)")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
    lyr = ds.CreateLayer("edits", None, ogr.wkbNone)
    for f in EDIT_FIELDS:
        lyr.CreateField(ogr.FieldDefn(f, ogr.OFTString))
    gtypes = {"point": ogr.wkbPoint, "line": ogr.wkbMultiLineString, "polygon": ogr.wkbMultiPolygon}
    for name, kind in ADD_LAYERS.items():
        lyr = ds.CreateLayer(name, srs, gtypes[kind])
        for f in ADD_FIELDS:
            lyr.CreateField(ogr.FieldDefn(f, ogr.OFTString))
    ds = None
    return path


def load(path: Path) -> tuple[list[dict], list[dict]]:
    """Read edits and additions. Missing file -> nothing to apply."""
    from osgeo import gdal, ogr

    gdal.UseExceptions()
    if not path.exists():
        return [], []
    ds = ogr.Open(str(path))
    edits, adds = [], []
    lyr = ds.GetLayerByName("edits")
    if lyr is not None:
        for f in lyr:
            edits.append({k: (f.GetField(k) if f.GetFieldIndex(k) >= 0 else None) for k in EDIT_FIELDS})
    for name, kind in ADD_LAYERS.items():
        lyr = ds.GetLayerByName(name)
        if lyr is None:
            continue
        for f in lyr:
            g = f.GetGeometryRef()
            adds.append({**{k: (f.GetField(k) if f.GetFieldIndex(k) >= 0 else None) for k in ADD_FIELDS},
                         "kind": kind, "geom": g.Clone() if g else None, "from_layer": name})
    return edits, adds


def apply(features: list[Feature], layer_kinds: dict[str, str], layer_fields: dict[str, list[str]],
          edits: list[dict], adds: list[dict]) -> tuple[list[Feature], dict]:
    """Apply additions then edits. Returns (features, report)."""
    report: dict[str, Any] = {"added": 0, "edited": 0, "deleted": 0, "hidden": 0, "closed": 0,
                              "stale": [], "errors": []}
    seen_ids: set[str] = set()

    def err(item: dict, msg: str) -> None:
        report["errors"].append({"id": item.get("id"), "error": msg})

    for a in adds:
        aid = (a.get("id") or "").strip()
        tgt = (a.get("target_layer") or "").strip()
        if not aid:
            err(a, f"{a['from_layer']}: feature without an id")
            continue
        if aid in seen_ids:
            err(a, f"duplicate id '{aid}'")
            continue
        seen_ids.add(aid)
        if tgt not in layer_kinds:
            err(a, f"unknown target_layer '{tgt}'")
            continue
        if layer_kinds[tgt] != a["kind"]:
            err(a, f"target_layer '{tgt}' holds {layer_kinds[tgt]}s, not {a['kind']}s")
            continue
        if a["geom"] is None or a["geom"].IsEmpty():
            err(a, "no geometry")
            continue
        try:
            extra = json.loads(a["attributes"]) if a.get("attributes") else {}
            if not isinstance(extra, dict):
                raise ValueError("not an object")
        except ValueError as e:
            err(a, f"attributes is not a JSON object ({e})")
            continue
        unknown = set(extra) - set(layer_fields[tgt])
        if unknown:
            err(a, f"attributes {sorted(unknown)} are not fields of '{tgt}'")
            continue
        props = {k: None for k in layer_fields[tgt]}
        props.update(extra)
        props.update({"source": "corrections", "source_id": f"corr:{aid}", "class": a.get("class"),
                      "name": a.get("name"), "hidden": 0, "closed": 0, "closure_note": None})
        features.append(Feature(tgt, a["geom"], props))
        report["added"] += 1

    by_id: dict[str, list[Feature]] = {}
    for f in features:
        by_id.setdefault(f.source_id, []).append(f)
    deleted: set[int] = set()
    for e in edits:
        eid = (e.get("id") or "").strip()
        if eid and eid in seen_ids:
            err(e, f"duplicate id '{eid}'")
            continue
        seen_ids.add(eid)
        action = (e.get("action") or "").strip().lower()
        sid = (e.get("source_id") or "").strip()
        if action not in ACTIONS:
            err(e, f"unknown action '{action}' (use one of {', '.join(ACTIONS)})")
            continue
        targets = [f for f in by_id.get(sid, []) if not e.get("layer") or f.layer == e["layer"]]
        targets = [f for f in targets if id(f) not in deleted]
        if not targets:
            report["stale"].append({k: e.get(k) for k in ("id", "source_id", "layer", "action", "field", "value", "note")})
            continue
        for f in targets:
            if action == "delete":
                deleted.add(id(f))
                report["deleted"] += 1
            elif action == "hide":
                f.props["hidden"] = 1
                report["hidden"] += 1
            elif action == "close":
                f.props["closed"] = 1
                f.props["closure_note"] = e.get("value") or e.get("note")
                report["closed"] += 1
            else:  # set
                fld = (e.get("field") or "").strip()
                if fld not in layer_fields[f.layer] and fld not in ("class", "name"):
                    err(e, f"'{fld}' is not a field of layer '{f.layer}'")
                    continue
                f.props[fld] = e.get("value")
                report["edited"] += 1
    return [f for f in features if id(f) not in deleted], report
