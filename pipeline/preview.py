"""Render a PNG preview of the terrain products through QGIS (headless).

Run as `python -m pipeline.preview --terrain-dir build/terrain`. It is a
separate process because QGIS may crash on teardown.

The preview is rendered at true map scale (config `scale`) and
`terrain.preview_dpi`, so line widths in millimetres look as they will in
print, just at lower resolution. Symbols here are a stand-in until the QGIS
project and docs/symbol-spec.md arrive in milestone 4.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

CONTOUR_COLOUR = "#9c5a2c"
HILLSHADE_OPACITY = 0.55


def render(root: Path, terrain_dir: Path) -> Path:
    from qgis.core import (
        QgsApplication, QgsCoordinateReferenceSystem, QgsLineSymbol, QgsMapRendererParallelJob,
        QgsMapSettings, QgsMarkerSymbol, QgsRasterLayer, QgsRectangle, QgsRuleBasedRenderer,
        QgsSingleSymbolRenderer, QgsVectorLayer, QgsContrastEnhancement,
    )
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor, QPainter

    sys.path.insert(0, str(root))
    from pipeline import config

    cfg = config.load(root)
    t = cfg.map["terrain"]
    dpi = float(t.get("preview_dpi", 100))

    app = QgsApplication([], False)
    app.initQgis()

    hs = QgsRasterLayer(str(terrain_dir / "hillshade.tif"), "hillshade")
    if not hs.isValid():
        raise RuntimeError("hillshade.tif failed to load")
    hs.renderer().setOpacity(HILLSHADE_OPACITY)
    ce = hs.renderer().contrastEnhancement()
    if ce is not None:
        ce.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum)
        ce.setMinimumValue(0)
        ce.setMaximumValue(255)
    hs.setBlendMode(QPainter.CompositionMode_Multiply)

    cont = QgsVectorLayer(f"{terrain_dir / 'contours.gpkg'}|layername=contours", "contours", "ogr")
    if not cont.isValid():
        raise RuntimeError("contours.gpkg failed to load")
    root_rule = QgsRuleBasedRenderer.Rule(None)
    for expr, width in (('"index" = 1', "0.25"), ('"index" = 0', "0.1")):
        sym = QgsLineSymbol.createSimple({"color": CONTOUR_COLOUR, "width": width, "width_unit": "MM",
                                          "capstyle": "round", "joinstyle": "round"})
        root_rule.appendChild(QgsRuleBasedRenderer.Rule(sym, 0, 0, expr))
    cont.setRenderer(QgsRuleBasedRenderer(root_rule))

    spots = QgsVectorLayer(f"{terrain_dir / 'spot_heights.gpkg'}|layername=spot_height_candidates", "spots", "ogr")
    layers = [cont, hs]
    if spots.isValid():
        spots.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
            {"name": "cross2", "color": "black", "size": "1.2", "size_unit": "MM", "outline_width": "0.2"})))
        layers.insert(0, spots)

    xmin, ymin, xmax, ymax = cfg.aoi
    px_per_m = dpi / 0.0254 / cfg.scale
    ms = QgsMapSettings()
    ms.setDestinationCrs(QgsCoordinateReferenceSystem(cfg.crs))
    ms.setLayers(layers)
    ms.setBackgroundColor(QColor("white"))
    ms.setOutputDpi(dpi)
    ms.setOutputSize(QSize(round((xmax - xmin) * px_per_m), round((ymax - ymin) * px_per_m)))
    ms.setExtent(QgsRectangle(xmin, ymin, xmax, ymax))

    job = QgsMapRendererParallelJob(ms)
    job.start()
    job.waitForFinished()
    out = terrain_dir / "preview.png"
    if not job.renderedImage().save(str(out), "PNG"):
        raise RuntimeError(f"failed to write {out}")
    print(f"preview: {out} ({ms.outputSize().width()} x {ms.outputSize().height()} px, "
          f"1:{cfg.scale:,} at {dpi:g} dpi, scale {ms.scale():,.0f})")
    sys.stdout.flush()
    os._exit(0)  # skip QGIS teardown, which can segfault headless


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=".")
    p.add_argument("--terrain-dir", default="build/terrain")
    a = p.parse_args()
    render(Path(a.root).resolve(), Path(a.terrain_dir).resolve())


if __name__ == "__main__":
    main()
