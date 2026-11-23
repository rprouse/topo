"""Print layout for one side of the sheet, generated at render time.

Geometry comes from map.yaml: the page is the sheet size, the map frame is
the side's extent at the map scale (so the scale is exact), with a title band
above and a collar band below. The legend sits in a box over one corner of
the map. Text in the collar (attribution, declination, edition, disclaimer)
is generated, never hand-edited.

To change the collar design, edit this module (and docs/symbol-spec.md);
the QGIS project only supplies the map layers, styles and labels.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config, Side

MM_PER_IN = 25.4


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


@dataclass(frozen=True)
class SheetGeometry:
    page_w: float
    page_h: float
    map: Rect
    title: Rect
    collar: Rect


def sheet_geometry(cfg: Config, side: Side, scale: int | None = None) -> SheetGeometry:
    scale = scale or cfg.scale
    sheet = cfg.map["sheet"]
    lay = cfg.map.get("layout", {})
    pw, ph = sheet["width_in"] * MM_PER_IN, sheet["height_in"] * MM_PER_IN
    if sheet.get("orientation", "portrait") == "landscape":
        pw, ph = max(pw, ph), min(pw, ph)
    else:
        pw, ph = min(pw, ph), max(pw, ph)
    margin = float(lay.get("page_margin_mm", 8))
    title_h = float(lay.get("title_band_mm", 14))
    collar_h = float(lay.get("collar_band_mm", 40))

    x0, y0, x1, y1 = side.extent
    mw = (x1 - x0) / scale * 1000.0
    mh = (y1 - y0) / scale * 1000.0
    avail_w = pw - 2 * margin
    avail_h = ph - 2 * margin - title_h - collar_h
    if mw > avail_w + 1e-6 or mh > avail_h + 1e-6:
        raise LayoutError(
            f"sheet {side.id}: {(x1 - x0) / 1000:.1f} x {(y1 - y0) / 1000:.1f} km at 1:{scale:,} needs a "
            f"{mw:.0f} x {mh:.0f} mm map frame, but the sheet leaves {avail_w:.0f} x {avail_h:.0f} mm. "
            f"Shrink the side's extent, change the scale, or reduce the layout bands in map.yaml.")
    total_h = title_h + mh + collar_h
    top = (ph - total_h) / 2
    left = (pw - mw) / 2
    return SheetGeometry(
        page_w=pw, page_h=ph,
        title=Rect(left, top, mw, title_h),
        map=Rect(left, top + title_h, mw, mh),
        collar=Rect(left, top + title_h + mh, mw, collar_h),
    )


def neighbours(cfg: Config, side: Side) -> list[tuple[str, str]]:
    """Faces sharing an edge with `side`: [(id, 'north'|'south'|'east'|'west')].

    A shared edge must run along at least half of the shorter face, so a
    diagonal neighbour, which only overlaps by the grid's overlap strip,
    doesn't count."""
    sx0, sy0, sx1, sy1 = side.extent
    out = []
    for o in cfg.sides:
        if o.id == side.id:
            continue
        ox0, oy0, ox1, oy1 = o.extent
        run_y = min(sy1, oy1) - max(sy0, oy0)
        run_x = min(sx1, ox1) - max(sx0, ox0)
        long_y = run_y >= 0.5 * min(sy1 - sy0, oy1 - oy0)
        long_x = run_x >= 0.5 * min(sx1 - sx0, ox1 - ox0)
        if long_y and ox1 > sx1 and ox0 <= sx1 + 1:
            out.append((o.id, "east"))
        elif long_y and ox0 < sx0 and ox1 >= sx0 - 1:
            out.append((o.id, "west"))
        elif long_x and oy1 > sy1 and oy0 <= sy1 + 1:
            out.append((o.id, "north"))
        elif long_x and oy0 < sy0 and oy1 >= sy0 - 1:
            out.append((o.id, "south"))
    return out


OVERVIEW_SCALES = (50_000, 60_000, 75_000, 100_000, 125_000, 150_000, 200_000, 250_000, 500_000)


def overview_geometry(cfg: Config) -> tuple[int, SheetGeometry]:
    """The overview face: the whole AOI at the largest round scale that fits
    the same sheet and bands."""
    x0, y0, x1, y1 = cfg.aoi
    probe = Side("overview", "", cfg.aoi)
    for scale in OVERVIEW_SCALES:
        try:
            return scale, sheet_geometry(cfg, probe, scale=scale)
        except LayoutError:
            continue
    raise LayoutError(f"the AOI ({(x1 - x0) / 1000:.0f} x {(y1 - y0) / 1000:.0f} km) doesn't fit the sheet "
                      f"even at 1:{OVERVIEW_SCALES[-1]:,}")


# --------------------------------------------------------------------------
# QGIS layout (only called inside the QGIS process)
# --------------------------------------------------------------------------

def build_layout(project, cfg: Config, side: Side, texts: dict, north: dict, *, overview=None):
    """Create the print layout for one side. `texts` holds generated collar
    text (attribution, disclaimer, edition, declination); `north` the
    declination/convergence values for the diagram."""
    import math

    import qgis.core as qc
    from qgis.PyQt.QtCore import QPointF, QRectF, Qt
    from qgis.PyQt.QtGui import QColor, QFont, QPolygonF

    from . import style as S

    # `overview` = {"scale": int, "layers": [...], "faces": layer, "grid_m": float}
    # draws the whole set at a small scale with the faces outlined, and
    # skips the sheet index and neighbour notes.
    scale = overview["scale"] if overview else cfg.scale
    g = sheet_geometry(cfg, side, scale=scale)
    mm = qc.QgsUnitTypes.LayoutMillimeters

    layout = qc.QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(f"Side {side.id}")
    layout.pageCollection().page(0).setPageSize(qc.QgsLayoutSize(g.page_w, g.page_h, mm))

    def fmt(size, bold=False, italic=False, color="#1a1a1a"):
        f = QFont(S.FONT)
        f.setBold(bold)
        f.setItalic(italic)
        t = qc.QgsTextFormat()
        t.setFont(f)
        t.setSize(size)
        t.setSizeUnit(qc.Qgis.RenderUnit.Points)
        t.setColor(QColor(color))
        return t

    def text(item_id, value, rect: Rect, size, bold=False, italic=False, halign=Qt.AlignLeft, valign=Qt.AlignTop):
        lab = qc.QgsLayoutItemLabel(layout)
        lab.setId(item_id)
        lab.setText(value)
        lab.setTextFormat(fmt(size, bold, italic))
        lab.setHAlign(halign)
        lab.setVAlign(valign)
        layout.addLayoutItem(lab)
        lab.attemptSetSceneRect(QRectF(rect.x, rect.y, rect.w, rect.h))
        return lab

    # ---- map frame with UTM grid
    m = qc.QgsLayoutItemMap(layout)
    m.setId("map")
    layout.addLayoutItem(m)
    m.attemptSetSceneRect(QRectF(g.map.x, g.map.y, g.map.w, g.map.h))
    m.setCrs(qc.QgsCoordinateReferenceSystem(cfg.crs))
    x0, y0, x1, y1 = side.extent
    m.setExtent(qc.QgsRectangle(x0, y0, x1, y1))
    m.setScale(scale)
    if overview:
        m.setLayers(overview["layers"])
        m.setKeepLayerSet(True)
    m.setFrameEnabled(True)
    m.setFrameStrokeWidth(qc.QgsLayoutMeasurement(0.3, mm))
    m.setBackgroundColor(QColor("white"))

    spacing = float(overview["grid_m"]) if overview else float(cfg.map.get("grid", {}).get("spacing_m", 1000))
    grid = qc.QgsLayoutItemMapGrid("UTM grid", m)
    grid.setUnits(qc.QgsLayoutItemMapGrid.MapUnit)
    grid.setIntervalX(spacing)
    grid.setIntervalY(spacing)
    grid.setStyle(qc.QgsLayoutItemMapGrid.Solid)
    grid.setLineSymbol(qc.QgsLineSymbol.createSimple(
        {"line_color": "#1a1a1a", "line_width": str(S.MIN_LINE_MM), "line_width_unit": "MM"}))
    grid.setAnnotationEnabled(True)
    grid.setAnnotationFormat(qc.QgsLayoutItemMapGrid.CustomFormat)
    grid.setAnnotationExpression("to_string(round(@grid_number / 1000))")   # km, e.g. 640 / 5484
    grid.setAnnotationTextFormat(fmt(S.MIN_TEXT_PT))
    for border in (qc.QgsLayoutItemMapGrid.Left, qc.QgsLayoutItemMapGrid.Right,
                   qc.QgsLayoutItemMapGrid.Top, qc.QgsLayoutItemMapGrid.Bottom):
        grid.setAnnotationPosition(qc.QgsLayoutItemMapGrid.OutsideMapFrame, border)
        vertical = border in (qc.QgsLayoutItemMapGrid.Left, qc.QgsLayoutItemMapGrid.Right)
        grid.setAnnotationDirection(qc.QgsLayoutItemMapGrid.Vertical if vertical else qc.QgsLayoutItemMapGrid.Horizontal,
                                    border)
    grid.setAnnotationFrameDistance(1.0)
    grid.setFrameStyle(qc.QgsLayoutItemMapGrid.NoFrame)
    m.grids().addGrid(grid)
    m.updateBoundingRect()   # include the annotations outside the frame, or they are clipped away

    # ---- title band
    t = g.title
    text("title", texts["title"], Rect(t.x, t.y, t.w * 0.6, t.h - 4), 22, bold=True, valign=Qt.AlignVCenter)
    text("subtitle", texts["subtitle"], Rect(t.x + t.w * 0.4, t.y, t.w * 0.6, t.h - 4), 9,
         halign=Qt.AlignRight, valign=Qt.AlignVCenter)

    # ---- collar band: four columns
    c = g.collar
    top = c.y + 6                       # room for the bottom grid annotations
    h = c.h - 7
    cols = [0.34, 0.30, 0.22, 0.14]
    xs = [c.x]
    for f in cols[:-1]:
        xs.append(xs[-1] + c.w * f)
    gap = 4

    # column 1: scale bar, grid note, declination diagram and text
    sb = qc.QgsLayoutItemScaleBar(layout)
    sb.setId("scalebar")
    sb.setStyle("Single Box")
    sb.setLinkedMap(m)
    sb.setUnits(qc.Qgis.DistanceUnit.Kilometers)   # QGIS converts from map units (m) itself
    sb.setUnitLabel("km")
    sb.setSegmentSizeMode(qc.Qgis.ScaleBarSegmentSizeMode.Fixed)
    # Segment length: the largest round distance that is at most 45 mm on paper
    # (1 km at 1:25,000, 2 km at 1:75,000).
    seg_km = max([v for v in (0.5, 1, 2, 5, 10, 20) if v * 1e6 / scale <= 45] or [0.5])
    sb.setUnitsPerSegment(seg_km)
    sb.setNumberOfSegments(3)
    sb.setNumberOfSegmentsLeft(1)
    sb.setHeight(1.5)
    sb.setTextFormat(fmt(S.MIN_TEXT_PT))
    layout.addLayoutItem(sb)
    sb.attemptMove(qc.QgsLayoutPoint(xs[0], top, mm))
    sb.refresh()
    sb_h = sb.rect().height()

    diag = min(18.0, h - sb_h - 4)       # declination diagram height (mm), below the scale bar
    below = top + sb_h + 2
    text("scale_text", texts["scale_note"], Rect(xs[0], below, c.w * cols[0] - gap - diag - 4, h - sb_h - 2),
         S.MIN_TEXT_PT)
    cx, base = xs[0] + c.w * cols[0] - gap - diag / 2, below + diag

    def ray(angle_deg, length, arrow, item_id):
        a = math.radians(angle_deg)
        tip = QPointF(cx + length * math.sin(a), base - length * math.cos(a))
        pl = qc.QgsLayoutItemPolyline(QPolygonF([QPointF(cx, base), tip]), layout)
        pl.setId(item_id)
        pl.setSymbol(qc.QgsLineSymbol.createSimple({"line_color": "#1a1a1a", "line_width": "0.2", "line_width_unit": "MM"}))
        if arrow:
            pl.setEndMarker(qc.QgsLayoutItemPolyline.ArrowHead)
            pl.setArrowHeadWidth(1.2)
        layout.addLayoutItem(pl)
        return tip

    tn = ray(0, diag, False, "true_north")
    star = qc.QgsLayoutItemShape(layout)
    star.setShapeType(qc.QgsLayoutItemShape.Ellipse)
    star.setSymbol(qc.QgsFillSymbol.createSimple({"color": "#1a1a1a", "outline_style": "no"}))
    layout.addLayoutItem(star)
    star.attemptSetSceneRect(QRectF(tn.x() - 0.6, tn.y() - 0.6, 1.2, 1.2))
    gn = ray(north["convergence_deg"], diag * 0.8, False, "grid_north")
    mn = ray(north["declination_deg"], diag * 0.9, True, "magnetic_north")
    # Labels spread out: the angles are small, so the tips nearly coincide.
    text("tn_label", "TN", Rect(tn.x() - 3, tn.y() - 4.2, 6, 3), S.MIN_TEXT_PT, halign=Qt.AlignHCenter)
    text("gn_label", "GN", Rect(gn.x() - 7, gn.y() - 1.2, 6, 3), S.MIN_TEXT_PT, halign=Qt.AlignRight)
    text("mn_label", "MN", Rect(mn.x() + 1.2, mn.y() - 1.2, 6, 3), S.MIN_TEXT_PT)

    text("attribution", texts["attribution"], Rect(xs[1], top, c.w * cols[1] - gap, h), S.MIN_TEXT_PT)
    text("disclaimer", texts["disclaimer"], Rect(xs[2], top, c.w * cols[2] - gap, h), S.MIN_TEXT_PT)
    text("edition", texts["edition"], Rect(xs[3], top, c.w * cols[3], h), S.MIN_TEXT_PT, halign=Qt.AlignRight)

    # ---- sheet index: where this side sits on the whole sheet
    if len(cfg.sides) > 1 and not overview:
        ax0, ay0, ax1, ay1 = (min(s.extent[0] for s in cfg.sides), min(s.extent[1] for s in cfg.sides),
                              max(s.extent[2] for s in cfg.sides), max(s.extent[3] for s in cfg.sides))
        box_h = min(16.0, h - 12)
        k = min(box_h / (ay1 - ay0), (c.w * cols[3]) / (ax1 - ax0))
        bx = xs[3] + c.w * cols[3] - (ax1 - ax0) * k        # right-aligned under the edition text
        by = top + h - box_h
        for other in cfg.sides:
            r = Rect(bx + (other.extent[0] - ax0) * k, by + (ay1 - other.extent[3]) * k,
                     (other.extent[2] - other.extent[0]) * k, (other.extent[3] - other.extent[1]) * k)
            shp = qc.QgsLayoutItemShape(layout)
            shp.setShapeType(qc.QgsLayoutItemShape.Rectangle)
            shp.setSymbol(qc.QgsFillSymbol.createSimple({
                "color": "#d9d9d9" if other.id == side.id else "#ffffff", "outline_color": "#1a1a1a",
                "outline_width": "0.2", "outline_width_unit": "MM"}))
            layout.addLayoutItem(shp)
            shp.attemptSetSceneRect(QRectF(r.x, r.y, r.w, r.h))
            text(f"index_{other.id}", other.id, r, 7, bold=other.id == side.id,
                 halign=Qt.AlignHCenter, valign=Qt.AlignVCenter)
        text("index_note", "Sheet index", Rect(bx - 22, by + box_h - 3, 21, 3), S.MIN_TEXT_PT, halign=Qt.AlignRight)

    # ---- "continues on sheet X" along edges shared with another face
    for other_id, direction in ([] if overview else neighbours(cfg, side)):
        note = f"Continues on sheet {other_id}"
        gap = 9.0                                            # outside the grid annotations
        if direction == "east":
            lab = text(f"continues_{other_id}", note + " →", Rect(0, 0, 60, 4), 8, bold=True, halign=Qt.AlignHCenter)
            lab.setItemRotation(90)
            lab.attemptMove(qc.QgsLayoutPoint(g.map.right + gap, g.map.y + g.map.h / 2 - 30, mm))
        elif direction == "west":
            lab = text(f"continues_{other_id}", "← " + note, Rect(0, 0, 60, 4), 8, bold=True, halign=Qt.AlignHCenter)
            lab.setItemRotation(-90)
            lab.attemptMove(qc.QgsLayoutPoint(g.map.x - gap - 4, g.map.y + g.map.h / 2 + 30, mm))
        else:
            # Top and bottom: a small box just inside the frame (the title band
            # and collar leave no room outside it).
            y = g.map.y + 2.5 if direction == "north" else g.map.bottom - 2.5 - 5
            label_text = ("↑ " + note) if direction == "north" else (note + " ↓")
            lab = text(f"continues_{other_id}", label_text, Rect(g.map.x + g.map.w / 2 - 25, y, 50, 5), 8,
                       bold=True, halign=Qt.AlignHCenter, valign=Qt.AlignVCenter)
            lab.setBackgroundEnabled(True)
            lab.setBackgroundColor(QColor(255, 255, 255, 235))
            lab.setFrameEnabled(True)
            lab.setFrameStrokeWidth(qc.QgsLayoutMeasurement(0.2, mm))

    if overview:
        return layout                                   # the faces carry the legend

    # ---- legend box over a corner of the map
    leg = qc.QgsLayoutItemLegend(layout)
    leg.setId("legend")
    leg.setTitle("Legend")
    leg.setLinkedMap(m)
    leg.setAutoUpdateModel(False)
    root = leg.model().rootGroup()
    root.clear()
    for name in ("Roads", "Trails", "Railways", "Lifts", "Power lines", "Streams", "Lakes and rivers",
                 "Contours", "Points of interest", "Spot heights", "Protected areas", "Buildings"):
        layers = project.mapLayersByName(name)
        if layers:
            root.addLayer(layers[0])
    leg.setColumnCount(2)
    leg.setSplitLayer(True)
    for st, size, bold in ((qc.QgsLegendStyle.Title, 8, True), (qc.QgsLegendStyle.Group, 7, True),
                           (qc.QgsLegendStyle.Subgroup, 7, True), (qc.QgsLegendStyle.SymbolLabel, S.MIN_TEXT_PT, False)):
        leg.rstyle(st).setTextFormat(fmt(size, bold))
    leg.setSymbolWidth(7)
    leg.setSymbolHeight(3)
    leg.setBackgroundEnabled(True)
    leg.setBackgroundColor(QColor(255, 255, 255, 235))
    leg.setFrameEnabled(True)
    leg.setFrameStrokeWidth(qc.QgsLayoutMeasurement(0.2, mm))
    leg.setBoxSpace(2.5)
    leg.setResizeToContents(True)
    # Anchor the legend by its corner nearest the map corner, so however big
    # it grows, it grows into the map and never over the collar.
    inset = 4.0
    corner = cfg.map.get("layout", {}).get("legend_corner", "bottom_left")
    ref = {"top_left": qc.QgsLayoutItem.UpperLeft, "top_right": qc.QgsLayoutItem.UpperRight,
           "bottom_left": qc.QgsLayoutItem.LowerLeft, "bottom_right": qc.QgsLayoutItem.LowerRight}[corner]
    leg.setReferencePoint(ref)
    layout.addLayoutItem(leg)
    lx = g.map.x + inset if "left" in corner else g.map.right - inset
    ly = g.map.y + inset if "top" in corner else g.map.bottom - inset
    leg.attemptMove(qc.QgsLayoutPoint(lx, ly, mm))
    leg.adjustBoxSize()
    leg.attemptMove(qc.QgsLayoutPoint(lx, ly, mm))

    return layout
