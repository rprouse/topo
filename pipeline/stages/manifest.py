"""Stage 7: write build/manifest.json - exactly what this build used."""

from __future__ import annotations

import json
import logging

from .. import state, versions
from ..attribution import attribution_lines
from ..cache import utc_now
from ..config import Config, crs_name

log = logging.getLogger(__name__)

STAGES = ("fetch", "terrain", "vectors", "render", "proofs", "qa")


def run(cfg: Config) -> dict:
    stage_states = {s: st for s in STAGES if (st := state.read(cfg.build_dir, s))}
    fetched = stage_states.get("fetch", {}).get("sources", {})

    sources = {}
    for sid, rec in sorted(fetched.items()):
        src = cfg.sources[sid]
        sources[sid] = {
            "title": src.title,
            "licence": src.licence,
            "endpoint": src.get("endpoint"),
            "request": rec["request"],
            "urls": rec["urls"],
            "retrieved_at": rec["retrieved_at"],
            "feature_count": rec.get("feature_count"),
            "files": rec["files"],
            **({"osm_snapshot": rec["snapshot"], "osm_database_timestamp": rec["osm_base"]}
               if rec.get("osm_base") else {}),
        }

    manifest = {
        "name": cfg.map.get("name"),
        "edition": cfg.edition,
        "built_at": utc_now(),
        "map": {
            "crs": cfg.crs,
            "crs_name": crs_name(cfg.crs),
            "scale": cfg.scale,
            "aoi": list(cfg.aoi),
            "aoi_buffered": list(cfg.aoi_buffered),
            "sides": [{"id": s.id, "title": s.title, "extent": list(s.extent)} for s in cfg.sides],
            "contour_interval_m": cfg.map.get("terrain", {}).get("contour_interval_m"),
        },
        "osm_snapshot": cfg.map.get("osm", {}).get("snapshot"),
        "tools": versions.collect(cfg.root),
        "sources": sources,
        "attribution": attribution_lines(cfg, sources.keys()),
        "declination": (stage_states.get("render") or {}).get("declination"),
        "outputs": (stage_states.get("render") or {}).get("result", {}).get("sides"),
        "stages": {s: {"inputs_hash": st.get("inputs_hash"), "completed_at": st.get("completed_at")}
                   for s, st in stage_states.items()},
    }
    out = cfg.build_dir / "manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("wrote %s (%d sources)", out.relative_to(cfg.root), len(sources))
    return manifest
