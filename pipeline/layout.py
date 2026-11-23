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


def sheet_geometry(cfg: Config, side: Side) -> SheetGeometry:
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
    mw = (x1 - x0) / cfg.scale * 1000.0
    mh = (y1 - y0) / cfg.scale * 1000.0
    avail_w = pw - 2 * margin
    avail_h = ph - 2 * margin - title_h - collar_h
    if mw > avail_w + 1e-6 or mh > avail_h + 1e-6:
        raise LayoutError(
            f"side {side.id}: {(x1 - x0) / 1000:.1f} x {(y1 - y0) / 1000:.1f} km at 1:{cfg.scale:,} needs a "
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


# --------------------------------------------------------------------------
# QGIS layout (only called inside the QGIS process)
# --------------------------------------------------------------------------

def build_layout(project, cfg: Config, side: Side, texts: dict, north: dict):
    """Create the print layout for one side. `texts` holds generated collar
    text (attribution, disclaimer, edition, declination); `north` the
    declination/convergence values for the diagram."""
    import math

    import qgis.core as qc
    from qgis.PyQt.QtCore import QPointF, QRectF, Qt
    from qgis.PyQt.QtGui import QColor, QFont, QPolygonF

    from . import style as S

    g = sheet_geometry(cfg, side)
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
    m.setScale(cfg.scale)
    m.setFrameEnabled(True)
    m.setFrameStrokeWidth(qc.QgsLayoutMeasurement(0.3, mm))
    m.setBackgroundColor(QColor("white"))

    spacing = float(cfg.map.get("grid", {}).get("spacing_m", 1000))
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
    sb.setUnitsPerSegment(1)
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
