"""Evaluate config/osm_mapping.yaml: OSM tags -> map layer, class, attributes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

GEOMETRIES = ("point", "line", "polygon")
OSM_LAYERS = ("points", "lines", "multipolygons")
# GDAL OSM driver fields that are not OSM tags.
NON_TAG_FIELDS = {"osm_id", "osm_way_id", "other_tags", "z_order", "all_tags"}

_HSTORE = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')


class MappingError(ValueError):
    pass


def parse_hstore(s: str | None) -> dict[str, str]:
    """Parse GDAL's other_tags HSTORE string: "k"=>"v","k2"=>"v2"."""
    if not s:
        return {}
    unesc = lambda t: t.replace('\\"', '"').replace("\\\\", "\\")
    return {unesc(k): unesc(v) for k, v in _HSTORE.findall(s)}


def _check_cond(cond: Any, ctx: str) -> None:
    if not isinstance(cond, dict):
        raise MappingError(f"{ctx}: 'when' must be a mapping of tag conditions")
    for k, spec in cond.items():
        if isinstance(spec, dict):
            bad = set(spec) - {"regex", "not"}
            if bad or len(spec) != 1:
                raise MappingError(f"{ctx}: condition on '{k}' must be {{regex: ..}} or {{not: [..]}}")
            if "regex" in spec:
                re.compile(spec["regex"])


def matches(cond: dict | None, tags: dict[str, str]) -> bool:
    if not cond:
        return True
    for key, spec in cond.items():
        v = tags.get(key)
        if spec is True:
            ok = v is not None
        elif spec is False or spec is None:
            ok = v is None
        elif isinstance(spec, list):
            ok = v in [str(x) for x in spec]
        elif isinstance(spec, dict):
            if "regex" in spec:
                ok = v is not None and re.search(spec["regex"], v) is not None
            else:
                ok = v is None or v not in [str(x) for x in spec["not"]]
        else:
            ok = v == str(spec)
        if not ok:
            return False
    return True


@dataclass
class LayerMapping:
    name: str
    sources: tuple[str, ...]
    geometry: str
    rules: list[dict]
    attributes: dict[str, str] = field(default_factory=dict)
    derived: dict[str, list[dict]] = field(default_factory=dict)
    as_point: bool = False

    @property
    def fields(self) -> list[str]:
        return ["name", *self.attributes, *self.derived]

    def classify(self, tags: dict[str, str]) -> dict[str, Any] | None:
        """Return {class, name, attributes..., derived...} or None if no rule matches."""
        for rule in self.rules:
            if matches(rule.get("when"), tags):
                cls = rule["class"]
                if isinstance(cls, dict):
                    cls = tags.get(cls["tag"])
                out: dict[str, Any] = {"class": cls, "name": tags.get("name")}
                for fld, tag in self.attributes.items():
                    out[fld] = tags.get(tag)
                for fld, drules in self.derived.items():
                    out[fld] = next((r.get("value") for r in drules if matches(r.get("when"), tags)), None)
                return out
        return None


def load(path: Path) -> dict[str, LayerMapping]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    layers = doc.get("layers")
    if not isinstance(layers, dict) or not layers:
        raise MappingError(f"{path}: needs a non-empty 'layers' mapping")
    out = {}
    for name, d in layers.items():
        ctx = f"{path.name} layers.{name}"
        srcs = d.get("from") or []
        srcs = [srcs] if isinstance(srcs, str) else srcs
        if not srcs or any(s not in OSM_LAYERS for s in srcs):
            raise MappingError(f"{ctx}: 'from' must list some of {OSM_LAYERS}")
        geom = d.get("geometry")
        if geom not in GEOMETRIES:
            raise MappingError(f"{ctx}: 'geometry' must be one of {GEOMETRIES}")
        if "multipolygons" in srcs and geom == "point" and not d.get("as_point"):
            raise MappingError(f"{ctx}: polygons into a point layer need 'as_point: true'")
        rules = d.get("rules") or []
        if not rules:
            raise MappingError(f"{ctx}: no rules")
        for i, r in enumerate(rules):
            if "class" not in r:
                raise MappingError(f"{ctx}.rules[{i}]: missing 'class'")
            if isinstance(r["class"], dict) and set(r["class"]) != {"tag"}:
                raise MappingError(f"{ctx}.rules[{i}]: class must be a string or {{tag: key}}")
            _check_cond(r.get("when", {}), f"{ctx}.rules[{i}]")
        derived = d.get("derived") or {}
        for fld, drules in derived.items():
            for i, r in enumerate(drules):
                _check_cond(r.get("when", {}), f"{ctx}.derived.{fld}[{i}]")
        attrs = {str(k): str(v) for k, v in (d.get("attributes") or {}).items()}
        clash = {"class", "name", "source", "source_id", "hidden", "closed", "closure_note"} & (set(attrs) | set(derived))
        if clash:
            raise MappingError(f"{ctx}: attribute names {sorted(clash)} are reserved")
        out[name] = LayerMapping(name=name, sources=tuple(srcs), geometry=geom, rules=rules,
                                 attributes=attrs, derived=derived, as_point=bool(d.get("as_point")))
    return out
