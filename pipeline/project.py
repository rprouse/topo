"""Generate the starting QGIS project: qgis/fernie.qgs (+ fernie.qgd).

Run with `make project-init` (once). The project holds the layers, their
styles and labels; after generation it belongs to you: restyle and place
labels in QGIS desktop (3.44.x) and commit qgis/. Re-running needs --force
and discards those edits.

Layers point at ../build/ with relative paths, so the project opens on any
machine after a build. Label positions you set by hand are stored in the
auxiliary storage (fernie.qgd), keyed on `source_id` rather than on feature
ids, so they survive rebuilds of map.gpkg.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_NAME = "fernie.qgs"


def _q():
    """Import the PyQGIS names used here (only inside the QGIS process)."""
    import qgis.core as qc
    from qgis.PyQt.QtGui import QColor, QFont, QPainter
    return qc, QColor, QFont, QPainter


# --------------------------------------------------------------------------
# Symbol helpers
# --------------------------------------------------------------------------

def line_layer(color, width, dash=None, cap="flat", pass_=0):
    qc, *_ = _q()
    props = {"line_color": color, "line_width": str(width), "line_width_unit": "MM",
             "capstyle": cap, "joinstyle": "round"}
    if dash:
        props |= {"use_custom_dash": "1", "customdash": f"{dash[0]};{dash[1]}", "customdash_unit": "MM"}
    sl = qc.QgsSimpleLineSymbolLayer.create(props)
    sl.setRenderingPass(pass_)
    return sl


def line_symbol(*layers):
    qc, *_ = _q()
    sym = qc.QgsLineSymbol()
    sym.deleteSymbolLayer(0)
    for sl in layers:
        sym.appendSymbolLayer(sl)
    return sym


def fill_symbol(color, outline=None, outline_width=0.1):
    qc, *_ = _q()
    props = {"color": color, "outline_style": "no"}
    if outline:
        props |= {"outline_style": "solid", "outline_color": outline, "outline_width": str(outline_width),
                  "outline_width_unit": "MM"}
    return qc.QgsFillSymbol.createSimple(props)


def marker_symbol(props):
    qc, *_ = _q()
    return qc.QgsMarkerSymbol.createSimple({"size_unit": "MM", "outline_width_unit": "MM", **props})


def rules(children, common_filter):
    """Rule-based renderer from (symbol, filter, legend label) children.

    `common_filter` (e.g. "not hidden") is ANDed into every rule rather than
    put on a parent rule, which would show up as an empty legend row. A child
    filter "ELSE" means "none of the other children"."""
    qc, *_ = _q()
    root = qc.QgsRuleBasedRenderer.Rule(None)
    others = [f for _, f, _ in children if f != "ELSE"]
    for sym, flt, label in children:
        if flt == "ELSE":
            flt = "NOT (" + " OR ".join(f"({o})" for o in others) + ")" if others else "TRUE"
        full = flt if common_filter in ("", "TRUE") else f"({common_filter}) AND ({flt})"
        root.appendChild(qc.QgsRuleBasedRenderer.Rule(sym, 0, 0, full, label))
    return qc.QgsRuleBasedRenderer(root), root


# --------------------------------------------------------------------------
# Label helpers
# --------------------------------------------------------------------------

def text_format(size, color="#1a1a1a", bold=False, italic=False, halo=0.6):
    qc, QColor, QFont, _ = _q()
    from pipeline import style as S

    font = QFont(S.FONT)
    font.setBold(bold)
    font.setItalic(italic)
    fmt = qc.QgsTextFormat()
    fmt.setFont(font)
    fmt.setSize(size)
    fmt.setSizeUnit(qc.Qgis.RenderUnit.Points)
    fmt.setColor(QColor(color))
    if halo:
        buf = qc.QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(halo)
        buf.setSizeUnit(qc.Qgis.RenderUnit.Millimeters)
        buf.setColor(QColor(S.C["label_halo"]))
        buf.setOpacity(0.85)
        fmt.setBuffer(buf)
    return fmt


def label(expr, fmt, placement, priority, *, line_flags=None, merge=False, repeat_mm=0.0,
          dist_mm=0.0, inside_only=False, wrap=None, size_expr=None, obstacle=True):
    qc, *_ = _q()
    s = qc.QgsPalLayerSettings()
    s.fieldName = expr
    s.isExpression = True
    s.setFormat(fmt)
    s.placement = placement
    s.priority = priority
    s.dist = dist_mm
    s.distUnits = qc.Qgis.RenderUnit.Millimeters
    if repeat_mm:
        s.repeatDistance = repeat_mm
        s.repeatDistanceUnit = qc.Qgis.RenderUnit.Millimeters
    ls = s.lineSettings()
    if line_flags is not None:
        ls.setPlacementFlags(line_flags)
    ls.setMergeLines(merge)
    s.setLineSettings(ls)
    if inside_only:
        s.fitInPolygonOnly = True
    if wrap:
        s.wrapChar = wrap
    if size_expr:
        s.dataDefinedProperties().setProperty(qc.QgsPalLayerSettings.Property.Size,
                                              qc.QgsProperty.fromExpression(size_expr))
    obs = s.obstacleSettings()
    obs.setIsObstacle(obstacle)
    s.setObstacleSettings(obs)
    return s


def rule_labeling(items):
    """items: list of (QgsPalLayerSettings, filter expression)."""
    qc, *_ = _q()
    root = qc.QgsRuleBasedLabeling.Rule(None)
    for settings, flt in items:
        root.appendChild(qc.QgsRuleBasedLabeling.Rule(settings, 0, 0, flt))
    return qc.QgsRuleBasedLabeling(root)


# --------------------------------------------------------------------------
# Layers
# --------------------------------------------------------------------------

def build(root: Path, force: bool = False) -> Path:
    qc, QColor, QFont, QPainter = _q()
    sys.path.insert(0, str(root))
    from pipeline import config
    from pipeline import style as S

    cfg = config.load(root)
    out = root / "qgis" / PROJECT_NAME
    if out.exists() and not force:
        raise FileExistsError(f"{out.relative_to(root)} exists; it is hand-edited. Use --force to regenerate it.")
    gpkg = cfg.build_dir / "map.gpkg"
    terrain = cfg.build_dir / "terrain"
    for p in (gpkg, terrain / "hillshade.tif", terrain / "contours.gpkg"):
        if not p.exists():
            raise FileNotFoundError(f"{p.relative_to(root)} is missing: run `make terrain vectors` first")

    app = qc.QgsApplication([], False)
    app.initQgis()
    project = qc.QgsProject.instance()
    project.clear()
    project.setFileName(str(out))
    project.setTitle(f"{cfg.map.get('title', 'Map')} topographic map")
    project.setCrs(qc.QgsCoordinateReferenceSystem(cfg.crs))
    project.writeEntryBool("Paths", "/Absolute", False)
    tree = project.layerTreeRoot()
    V, P = S.VISIBLE, qc.Qgis.LabelPlacement
    LF = qc.Qgis.LabelLinePlacementFlag

    def vec(layer_name, title, src=None):
        lyr = qc.QgsVectorLayer(f"{src or gpkg}|layername={layer_name}", title, "ogr")
        if not lyr.isValid():
            raise RuntimeError(f"layer {layer_name} failed to load")
        return lyr

    def add(lyr, labels=None):
        project.addMapLayer(lyr, False)
        tree.insertLayer(0, lyr)            # added bottom-up: each new layer goes on top
        if labels is not None:
            lyr.setLabeling(labels)
            lyr.setLabelsEnabled(True)
            idx = lyr.fields().indexOf("source_id")
            if idx >= 0:
                aux = project.auxiliaryStorage().createAuxiliaryLayer(lyr.fields().at(idx), lyr)
                if aux:
                    lyr.setAuxiliaryLayer(aux)
        return lyr

    # Landcover
    lc = vec("landcover", "Landcover")
    r, _ = rules([(fill_symbol(S.C[col]), f"\"class\" = '{cls}'", cls.capitalize()) for cls, col in S.LANDCOVER], V)
    lc.setRenderer(r)
    add(lc)

    # Hillshade as black shadow whose transparency follows the shading:
    # value 0 -> opaque black, 255 -> fully transparent. Drawn normally at
    # opacity o, this equals multiplying the tints below by the hillshade
    # (base * (1 - o * (1 - g))). A real multiply blend mode is avoided on
    # purpose: in PDF export it sends the whole map down a different render
    # path, which drops italic label fonts (and may rasterise vectors).
    hs = qc.QgsRasterLayer(str(terrain / "hillshade.tif"), "Hillshade")
    ramp = qc.QgsColorRampShader(0, 255)
    ramp.setColorRampType(qc.QgsColorRampShader.Interpolated)
    ramp.setColorRampItemList([qc.QgsColorRampShader.ColorRampItem(0, QColor(0, 0, 0, 255), "shade"),
                               qc.QgsColorRampShader.ColorRampItem(255, QColor(0, 0, 0, 0), "lit")])
    shader = qc.QgsRasterShader()
    shader.setRasterShaderFunction(ramp)
    hs_renderer = qc.QgsSingleBandPseudoColorRenderer(hs.dataProvider(), 1, shader)
    hs_renderer.setOpacity(S.HILLSHADE_OPACITY)
    hs.setRenderer(hs_renderer)
    add(hs)

    # Water areas
    wa = vec("water_areas", "Lakes and rivers")
    marsh = qc.QgsFillSymbol.createSimple({"style": "no", "outline_style": "no"})
    pat = qc.QgsLinePatternFillSymbolLayer()
    pat.setLineAngle(0)
    pat.setDistance(1.0)
    pat.setDistanceUnit(qc.Qgis.RenderUnit.Millimeters)
    pat.setSubSymbol(line_symbol(line_layer(S.C["water"], 0.12)))
    marsh.appendSymbolLayer(pat)
    r, _ = rules([(fill_symbol(S.C["water_fill"], S.C["water"], 0.1), "\"class\" IN ('lake', 'river')", "Lake / river"),
                  (marsh, "\"class\" = 'wetland'", "Wetland")], V)
    wa.setRenderer(r)
    add(wa, rule_labeling([
        (label('"name"', text_format(8, S.C["water_text"], italic=True), P.Horizontal, S.PRIORITY["water_areas"],
               inside_only=True), "\"class\" = 'lake' AND \"name\" IS NOT NULL"),
    ]))

    # Contours
    ct = vec("contours", "Contours", src=terrain / "contours.gpkg")
    r, _ = rules([(line_symbol(line_layer(S.C["contour"], 0.25, cap="round")), '"index" = 1', "Index contour"),
                  (line_symbol(line_layer(S.C["contour"], 0.12, cap="round")), "ELSE", "Contour")], "TRUE")
    ct.setRenderer(r)
    add(ct, rule_labeling([
        (label('to_string(round("elev"))', text_format(S.MIN_TEXT_PT, S.C["contour"], halo=0.5), P.Curved,
               S.PRIORITY["contours"], line_flags=LF.OnLine, repeat_mm=120, obstacle=False), '"index" = 1'),
    ]))

    # Streams
    wl = vec("water_lines", "Streams")
    r, _ = rules([
        (line_symbol(line_layer(S.C["water"], 0.35, cap="round")), "\"class\" = 'stream' AND \"stream_order\" >= 5", "River"),
        (line_symbol(line_layer(S.C["water"], 0.22, cap="round")), "\"class\" = 'stream' AND \"stream_order\" >= 3", "Creek"),
        (line_symbol(line_layer(S.C["water"], 0.15, cap="round")), "\"class\" IN ('stream', 'canal')", "Stream"),
        (line_symbol(line_layer(S.C["water"], 0.12, dash=(1.2, 0.5))), "\"class\" = 'intermittent'", "Intermittent stream"),
    ], V)
    wl.setRenderer(r)
    add(wl, rule_labeling([
        (label('"name"', text_format(7, S.C["water_text"], italic=True), P.Curved, S.PRIORITY["water_lines"],
               line_flags=LF.AboveLine | LF.BelowLine, merge=True, repeat_mm=150, dist_mm=0.3), '"name" IS NOT NULL'),
    ]))

    # Protected areas (outline only)
    pa = vec("protected_areas", "Protected areas")
    pa_sym = qc.QgsFillSymbol.createSimple({"color": "#00000000", "outline_color": S.C["protected"],
                                            "outline_width": "0.5", "outline_width_unit": "MM", "outline_style": "dash"})
    r, _ = rules([(pa_sym, "TRUE", "Park / protected area")], V)
    pa.setRenderer(r)
    add(pa)

    b = vec("buildings", "Buildings")
    r, _ = rules([(fill_symbol(S.C["building"]), "TRUE", "Building")], V)
    b.setRenderer(r)
    add(b)

    pw = vec("power_lines", "Power lines")
    r, _ = rules([(line_symbol(line_layer(S.C["power"], 0.15)), "TRUE", "Power line")], V)
    pw.setRenderer(r)
    add(pw)

    rw = vec("railways", "Railways")
    r, _ = rules([(line_symbol(line_layer(S.C["black"], 0.5), line_layer("#ffffff", 0.28, dash=(3, 3), pass_=1)),
                   "TRUE", "Railway")], V)
    r.setUsingSymbolLevels(True)
    rw.setRenderer(r)
    add(rw)

    # Roads: casings first (pass 0), then fills from minor to major.
    rd = vec("roads", "Roads")
    kids = []
    for i, (cls, lab, casing, spec) in enumerate(S.ROADS, start=1):
        layers = []
        if casing:
            layers.append(line_layer(casing[0], casing[1], pass_=0))
        layers.append(line_layer(spec["color"], spec["width"], dash=spec.get("dash"), pass_=i))
        kids.insert(0, (line_symbol(*layers), f"\"class\" = '{cls}'", lab))   # legend: major roads first
    r, _ = rules(kids, V)
    r.setUsingSymbolLevels(True)
    rd.setRenderer(r)
    add(rd, rule_labeling([
        (label('coalesce("name", "ref")', text_format(S.MIN_TEXT_PT), P.Curved, S.PRIORITY["roads"],
               line_flags=LF.AboveLine | LF.BelowLine, merge=True, repeat_mm=150, dist_mm=0.4),
         'coalesce("name", "ref") IS NOT NULL'),
    ]))

    lf = vec("lifts", "Lifts")
    r, _ = rules([(line_symbol(line_layer(S.C["black"], 0.3)), "TRUE", "Ski lift")], V)
    lf.setRenderer(r)
    add(lf)

    # Trails
    tr = vec("trails", "Trails")
    kids = [(line_symbol(line_layer(col, w, dash=d, cap="flat")), flt, lab) for flt, lab, col, w, d in S.TRAILS]
    r, root_rule = rules(kids, V)
    closed = marker_symbol({"name": "cross2", "color": S.C["black"], "size": "1.6", "outline_width": "0.3"})
    closed_line = qc.QgsLineSymbol()
    closed_line.deleteSymbolLayer(0)
    ml = qc.QgsMarkerLineSymbolLayer()
    ml.setInterval(8)
    ml.setIntervalUnit(qc.Qgis.RenderUnit.Millimeters)
    ml.setSubSymbol(closed)
    closed_line.appendSymbolLayer(ml)
    root_rule.appendChild(qc.QgsRuleBasedRenderer.Rule(closed_line, 0, 0, f'{V} AND "closed" = 1', "Closed"))
    tr.setRenderer(r)
    add(tr, rule_labeling([
        (label('"name"', text_format(S.MIN_TEXT_PT, "#8b1a1a"), P.Curved, S.PRIORITY["trails"],
               line_flags=LF.AboveLine | LF.BelowLine, merge=True, repeat_mm=120, dist_mm=0.4), '"name" IS NOT NULL'),
    ]))

    sp = vec("spot_heights", "Spot heights")
    r, _ = rules([(marker_symbol({"name": "circle", "color": S.C["black"], "size": "0.6", "outline_style": "no"}),
                   "TRUE", "Spot height")], V)
    sp.setRenderer(r)
    add(sp, rule_labeling([
        (label('to_string(round("elev"))', text_format(S.MIN_TEXT_PT), P.OrderedPositionsAroundPoint,
               S.PRIORITY["spot_heights"], dist_mm=0.5), "TRUE"),
    ]))

    po = vec("pois", "Points of interest")
    r, _ = rules([(marker_symbol(props), f"\"class\" = '{cls}'", lab) for cls, lab, props in S.POIS], V)
    po.setRenderer(r)
    add(po, rule_labeling([
        (label("\"name\" || CASE WHEN \"ele\" IS NOT NULL THEN '\\n' || to_string(round(\"ele\")) ELSE '' END",
               text_format(7.5, bold=True), P.OrderedPositionsAroundPoint, S.PRIORITY["peaks"], dist_mm=0.6),
         "\"class\" = 'peak' AND \"name\" IS NOT NULL"),
        (label("to_string(round(\"ele\"))", text_format(S.MIN_TEXT_PT), P.OrderedPositionsAroundPoint,
               S.PRIORITY["spot_heights"], dist_mm=0.6), "\"class\" = 'peak' AND \"name\" IS NULL AND \"ele\" IS NOT NULL"),
        (label('"name"', text_format(S.MIN_TEXT_PT), P.OrderedPositionsAroundPoint, S.PRIORITY["pois"], dist_mm=0.6),
         "\"class\" NOT IN ('peak', 'parking') AND \"name\" IS NOT NULL"),
    ]))

    pl = vec("places", "Places")
    pl.setRenderer(qc.QgsNullSymbolRenderer())
    size_expr = "CASE " + " ".join(f"WHEN \"class\" = '{k}' THEN {v}" for k, v in S.PLACE_SIZES.items()) + " ELSE 7 END"
    add(pl, rule_labeling([
        (label('"name"', text_format(9, S.C["place_text"], bold=True, halo=0.8), P.OverPoint, S.PRIORITY["places"],
               size_expr=size_expr), f'{V} AND "name" IS NOT NULL'),
    ]))

    nm = vec("names", "Official names")
    nm.setRenderer(qc.QgsNullSymbolRenderer())
    add(nm, rule_labeling([
        (label('"name"', text_format(7.5, "#5a4632", italic=True), P.OverPoint, S.PRIORITY["names"]),
         f"{V} AND \"matched\" = 0 AND \"concise\" IN ('MTN', 'VALL', 'PARK')"),
    ]))

    out.parent.mkdir(parents=True, exist_ok=True)
    if not project.write(str(out)):
        raise RuntimeError(f"failed to write {out}")
    print(f"wrote {out.relative_to(root)} with {len(project.mapLayers())} layers")
    sys.stdout.flush()
    os._exit(0)  # skip QGIS teardown (can crash headless)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=".")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    try:
        build(Path(a.root).resolve(), force=a.force)
    except (FileExistsError, FileNotFoundError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
