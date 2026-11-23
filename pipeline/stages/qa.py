"""Stage 5b: build/qa-report.md, the ground-truthing to-do list.

Sections: summary, terrain sources and gaps, changes since the baseline map
(added / removed / reclassified / renamed / moved roads, trails, points and
places, with OSM links and grid references), corrections that need
attention, OSM vs official names, and the attribution block exactly as
generated for the collar.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from .. import state
from ..attribution import attribution_lines
from ..cache import stable_hash
from ..config import Config

log = logging.getLogger(__name__)

OSM_URL = "https://www.openstreetmap.org/{kind}/{id}"
WATCH_FIELDS = {"roads": ["class", "name", "surface", "access"],
                "trails": ["class", "difficulty", "name", "sac_scale", "access"],
                "pois": ["class", "name", "ele"],
                "places": ["class", "name"]}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def grid_ref(cfg: Config, x: float, y: float) -> str:
    """'640.3 E 5484.7 N (side B)': km on the map's UTM grid, plus the side."""
    side = next((s.id for s in cfg.sides if s.extent[0] <= x <= s.extent[2] and s.extent[1] <= y <= s.extent[3]), None)
    return f"{x / 1000:.1f} E {y / 1000:.1f} N" + (f" (side {side})" if side else " (off sheet)")


def link(source_id: str) -> str:
    """Markdown link for OSM features, plain id otherwise."""
    if source_id and source_id.startswith("osm:"):
        kind, oid = source_id[4:].split("/", 1)
        return f"[{source_id}]({OSM_URL.format(kind=kind, id=oid)})"
    return f"`{source_id}`"


def md_table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return "_None._\n"
    esc = lambda v: "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def read_layer(path: Path, layer: str) -> dict[str, dict]:
    """source_id -> {fields..., _geom} for one layer of a map.gpkg."""
    from osgeo import gdal, ogr

    gdal.UseExceptions()
    ds = ogr.Open(str(path))
    lyr = ds.GetLayerByName(layer) if ds else None
    out: dict[str, dict] = {}
    if lyr is None:
        return out
    defn = lyr.GetLayerDefn()
    names = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    for f in lyr:
        g = f.GetGeometryRef()
        out[f.GetField("source_id")] = {**{n: f.GetField(n) for n in names}, "_geom": g.Clone() if g else None}
    ds = None
    return out


def _point(g):
    p = g.PointOnSurface() if g.GetGeometryName() != "POINT" else g
    return p.GetX(), p.GetY()


def compare(cfg: Config, new: Path, old: Path) -> list[dict]:
    """Changes between two map.gpkg files, by source_id."""
    q = cfg.map.get("qa", {})
    moved = float(q.get("moved_threshold_m", 25))
    lfrac = float(q.get("length_change_threshold", 0.10))
    rows = []
    for layer in q.get("change_layers", list(WATCH_FIELDS)):
        a, b = read_layer(old, layer), read_layer(new, layer)
        fields = WATCH_FIELDS.get(layer, ["class", "name"])
        for sid in sorted(set(a) | set(b)):
            o, n = a.get(sid), b.get(sid)
            ref_feat = n or o
            if ref_feat["_geom"] is None:
                continue
            x, y = _point(ref_feat["_geom"])
            base = {"layer": layer, "source_id": sid, "name": (n or o).get("name"), "class": (n or o).get("class"),
                    "grid": grid_ref(cfg, x, y)}
            if o is None:
                rows.append({**base, "change": "added", "detail": ""})
                continue
            if n is None:
                rows.append({**base, "change": "removed", "detail": ""})
                continue
            diffs = [f"{f}: {o.get(f)!s} → {n.get(f)!s}" for f in fields if (o.get(f) or None) != (n.get(f) or None)]
            if diffs:
                kind = "reclassified" if any(d.startswith(("class", "difficulty", "sac_scale")) for d in diffs) else (
                    "renamed" if any(d.startswith("name") for d in diffs) else "attributes")
                rows.append({**base, "change": kind, "detail": "; ".join(diffs)})
            go, gn = o["_geom"], n["_geom"]
            if go is None or gn is None:
                continue
            if gn.GetGeometryName() == "POINT":
                d = go.Distance(gn)
                if d > moved:
                    rows.append({**base, "change": "moved", "detail": f"{d:.0f} m"})
            elif "LINE" in gn.GetGeometryName():
                lo, ln = go.Length(), gn.Length()
                if lo > 0 and abs(ln - lo) / lo > lfrac:
                    rows.append({**base, "change": "reshaped", "detail": f"length {lo:.0f} → {ln:.0f} m"})
    return rows


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def run(cfg: Config) -> Path:
    b = cfg.build_dir
    fetch = state.read(b, "fetch") or {}
    terrain = json.loads((b / "terrain" / "terrain-report.json").read_text(encoding="utf-8")) \
        if (b / "terrain" / "terrain-report.json").exists() else {}
    vectors = json.loads((b / "vectors-report.json").read_text(encoding="utf-8")) \
        if (b / "vectors-report.json").exists() else {}
    render = state.read(b, "render") or {}
    proofs = state.read(b, "proofs") or {}
    osm = vectors.get("osm") or {}
    L: list[str] = []
    add = L.append

    add(f"# QA report: {cfg.map.get('title', 'Map')} edition {cfg.edition}\n")
    add(f"Generated {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
        f"OpenStreetMap snapshot {osm.get('snapshot')} (database as of {osm.get('osm_base')}). "
        f"Grid references are km on the {cfg.crs} grid printed on the map.\n")

    # ---- to-do summary
    changes: list[dict] = []
    base_path = cfg.root / cfg.map.get("qa", {}).get("baseline", "build/map.previous.gpkg")
    if (b / "map.gpkg").exists() and base_path.exists():
        changes = compare(cfg, b / "map.gpkg", base_path)
    corr = vectors.get("corrections", {})
    names = [n for n in vectors.get("names", []) if n["status"] != "match"]
    add("## To do\n")
    add(md_table(["Item", "Count", "Section"], [
        ["Changes since the baseline to check on the ground", len(changes), "[Changes](#changes-since-the-baseline)"],
        ["Stale corrections (their feature is gone)", len(corr.get("stale", [])), "[Corrections](#corrections)"],
        ["Corrections with errors", len(corr.get("errors", [])), "[Corrections](#corrections)"],
        ["Name differences (OSM vs official)", len(names), "[Names](#names-osm-vs-official)"],
        ["Terrain filled from low-resolution data (% of AOI)",
         next((e["aoi_percent"] for e in terrain.get("by_source", []) if e["source_id"] == "cdem"), 0),
         "[Terrain](#terrain)"],
    ]))

    # ---- terrain
    add("## Terrain\n")
    add(md_table(["Source", "Survey / date", "% of AOI", "% of grid"],
                 [[e["source_id"], e["vintage"], e["aoi_percent"], e["grid_percent"]] for e in terrain.get("by_source", [])]))
    add(f"\nInterpolated: {terrain.get('interpolated_aoi_percent', '?')}% of the AOI. "
        f"No data: {terrain.get('no_data_aoi_percent', '?')}%. "
        f"Elevation range {terrain.get('elevation_min_m')}–{terrain.get('elevation_max_m')} m. "
        f"See `build/terrain/coverage.png` for where each source was used. "
        f"Areas filled from CDEM are smoother than the lidar around them.\n")

    # ---- changes
    add("## Changes since the baseline\n")
    if not base_path.exists():
        add(f"_No baseline map at `{base_path.relative_to(cfg.root)}`: nothing to compare yet. "
            "The next vectors build keeps this one as the baseline._\n")
    else:
        add(f"Compared with `{base_path.relative_to(cfg.root)}`. Each row is something to check: on the ground, "
            "against imagery, or by asking someone who knows the area.\n")
        for layer in cfg.map.get("qa", {}).get("change_layers", []):
            rows = [c for c in changes if c["layer"] == layer]
            add(f"### {layer.capitalize()} ({len(rows)})\n")
            add(md_table(["Change", "Name", "Class", "Detail", "Where", "Feature"],
                         [[c["change"], c["name"], c["class"], c["detail"], c["grid"], link(c["source_id"])] for c in rows]))

    # ---- corrections
    add("## Corrections\n")
    add(f"Applied: {corr.get('added', 0)} added, {corr.get('edited', 0)} edited, {corr.get('hidden', 0)} hidden, "
        f"{corr.get('deleted', 0)} deleted, {corr.get('closed', 0)} closed.\n")
    add("### Stale corrections\n")
    add("Their source feature no longer exists (usually because OSM changed). Delete them or point them at the new feature.\n")
    add(md_table(["Edit", "Feature", "Action", "Field", "Value", "Note"],
                 [[s.get("id"), link(s.get("source_id")), s.get("action"), s.get("field"), s.get("value"), s.get("note")]
                  for s in corr.get("stale", [])]))
    add("### Errors\n")
    add(md_table(["Edit", "Problem"], [[e.get("id"), e.get("error")] for e in corr.get("errors", [])]))

    # ---- names
    add("## Names: OSM vs official\n")
    add("OSM names are printed. Where the official (Canadian Geographical Names) name differs, decide which "
        "the map should use and record it with a `set name` correction. `official_only` is an official name "
        "with no matching OSM feature (often a ridge or range).\n")
    add(md_table(["Status", "OSM name", "Official name", "Distance", "Where", "Feature"],
                 [[n["status"], n.get("name_osm"), n.get("name_official"),
                   f"{n['distance_m']} m" if n.get("distance_m") is not None else "",
                   grid_ref(cfg, n["x"], n["y"]), link(n.get("source_id") or n.get("official_id"))]
                  for n in sorted(names, key=lambda n: (n["status"], n.get("name_osm") or n.get("name_official") or ""))]))

    # ---- collar and outputs
    add("## Collar\n")
    add("Check that the printed collar matches this block exactly.\n")
    add("### Attribution\n")
    for line in attribution_lines(cfg, sorted(fetch.get("sources", {}))):
        add(f"> {line}\n>")
    decl = render.get("declination") or {}
    add(f"\n### Declination\n\n{decl.get('declination_deg')}° (east positive) for {decl.get('date')}, "
        f"changing {decl.get('annual_change_deg')}° per year; grid convergence {decl.get('convergence_deg')}°. "
        f"Model {decl.get('model')}.\n")
    add("### Disclaimer\n")
    disc = " ".join(str(cfg.map.get("collar", {}).get("disclaimer", "")).split())
    add(("**Still the placeholder.** " if "PLACEHOLDER" in disc else "") + disc + "\n")

    add("## Outputs\n")
    add(md_table(["Side", "Print PDF", "Digital GeoPDF", "Scale", "Proofs"],
                 [[s["id"], f"build/print/{s['pdf']}", f"build/digital/{s['geopdf']}" if s.get("geopdf") else "",
                   f"1:{s['map_scale']:,.0f}",
                   ", ".join(f"{p['file']} ({p['pages']} pages)" for p in proofs.get("sets", []) if p["side"] == s["id"])]
                  for s in (render.get("result") or {}).get("sides", [])]))

    out = b / "qa-report.md"
    b.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    state.write(b, "qa", stable_hash(L), {"changes": len(changes), "stale_corrections": len(corr.get("stale", [])),
                                          "name_differences": len(names)})
    log.info("qa: wrote %s (%d changes, %d stale corrections, %d name differences)",
             out.relative_to(cfg.root), len(changes), len(corr.get("stale", [])), len(names))
    return out
