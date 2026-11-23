"""Render the print layouts headless: `python -m pipeline.qgis_render --job job.json`.

Run by the render stage in a separate process (QGIS can crash on teardown).
The job file lists the sides and the generated collar text; this writes, per
side, build/print/side-<id>.pdf and a PNG preview, plus
build/print/render-project.qgs (the project with the generated layouts, for
inspecting in QGIS desktop) and a JSON result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def faces_layer(qc, cfg):
    """Memory layer with one rectangle per map face, labelled with its id,
    for the overview."""
    from qgis.PyQt.QtGui import QColor, QFont

    from pipeline import style as S

    lyr = qc.QgsVectorLayer(f"Polygon?crs={cfg.crs}&field=id:string&field=title:string", "Map sheets", "memory")
    feats = []
    for side in cfg.sides:
        f = qc.QgsFeature(lyr.fields())
        x0, y0, x1, y1 = side.extent
        f.setGeometry(qc.QgsGeometry.fromRect(qc.QgsRectangle(x0, y0, x1, y1)))
        f.setAttributes([side.id, side.title])
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    lyr.setRenderer(qc.QgsSingleSymbolRenderer(qc.QgsFillSymbol.createSimple({
        "color": "#00000000", "outline_color": "#1a1a1a", "outline_width": "0.6", "outline_width_unit": "MM"})))
    fmt = qc.QgsTextFormat()
    font = QFont(S.FONT)
    font.setBold(True)
    fmt.setFont(font)
    fmt.setNamedStyle("Bold")
    fmt.setSize(48)
    fmt.setSizeUnit(qc.Qgis.RenderUnit.Points)
    fmt.setColor(QColor(26, 26, 26, 200))
    buf = qc.QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.5)
    buf.setSizeUnit(qc.Qgis.RenderUnit.Millimeters)
    buf.setColor(QColor("white"))
    buf.setOpacity(0.8)
    fmt.setBuffer(buf)
    pal = qc.QgsPalLayerSettings()
    pal.fieldName = "id"
    pal.setFormat(fmt)
    pal.placement = qc.Qgis.LabelPlacement.OverPoint
    pal.priority = 10
    obs = pal.obstacleSettings()
    obs.setIsObstacle(False)
    pal.setObstacleSettings(obs)
    lyr.setLabeling(qc.QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)
    return lyr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    job = json.loads(Path(ap.parse_args().job).read_text(encoding="utf-8"))
    root = Path(job["root"])
    sys.path.insert(0, str(root))

    import qgis.core as qc

    from pipeline import config
    from pipeline.config import Side
    from pipeline.layout import build_layout

    cfg = config.load(root)
    out = Path(job["out_dir"])
    out.mkdir(parents=True, exist_ok=True)

    app = qc.QgsApplication([], False)
    app.initQgis()
    project = qc.QgsProject.instance()
    if not project.read(job["project"]):
        raise SystemExit(f"could not read {job['project']}")
    bad = [l.name() for l in project.mapLayers().values() if not l.isValid()]
    if bad:
        raise SystemExit(f"layers failed to load (run the earlier stages first): {', '.join(bad)}")

    lay = cfg.map.get("layout", {})
    dig = cfg.map.get("digital", {})
    results = {"qgis": qc.Qgis.version(), "sides": []}

    def export(layout, stem: str, geopdf: bool) -> dict:
        """Print PDF (lossless, 300 dpi), optional digital GeoPDF, PNG preview."""
        exporter = qc.QgsLayoutExporter(layout)
        pdf = out / f"{stem}.pdf"
        ps = qc.QgsLayoutExporter.PdfExportSettings()
        ps.dpi = float(lay.get("pdf_dpi", 300))
        ps.rasterizeWholeImage = False
        ps.forceVectorOutput = False
        ps.appendGeoreference = True
        ps.exportMetadata = True
        ps.simplifyGeometries = False
        ps.textRenderFormat = qc.Qgis.TextRenderFormat.AlwaysText
        if lay.get("lossless_images", True):
            ps.flags = layout.renderContext().flags() | qc.QgsLayoutRenderContext.FlagLosslessImageRendering
        if exporter.exportToPdf(str(pdf), ps) != qc.QgsLayoutExporter.Success:
            raise SystemExit(f"PDF export failed for {stem}: {exporter.errorMessage()}")
        geo = None
        if geopdf and dig.get("geopdf", True):
            # Digital edition: GeoPDF georeferenced to the map frame, lighter raster.
            geo = Path(job["digital_dir"]) / f"{stem}.pdf"
            geo.parent.mkdir(parents=True, exist_ok=True)
            gs_ = qc.QgsLayoutExporter.PdfExportSettings()
            gs_.dpi = float(dig.get("dpi", 200))
            gs_.writeGeoPdf = True
            gs_.includeGeoPdfFeatures = bool(dig.get("include_features", False))
            gs_.appendGeoreference = True
            gs_.exportMetadata = True
            gs_.textRenderFormat = qc.Qgis.TextRenderFormat.AlwaysText
            if exporter.exportToPdf(str(geo), gs_) != qc.QgsLayoutExporter.Success:
                raise SystemExit(f"GeoPDF export failed for {stem}: {exporter.errorMessage()}")
        # Preview: render the page straight to an image (exportToImage would try
        # to write georeferencing into the PNG, which GDAL can't).
        png = out / f"{stem}.png"
        if not exporter.renderPageToImage(0, dpi=float(lay.get("preview_dpi", 50))).save(str(png), "PNG"):
            raise SystemExit(f"PNG preview failed for {stem}")
        return {"pdf": pdf.name, "png": png.name, "geopdf": geo.name if geo else None,
                "map_scale": round(layout.itemById("map").scale(), 3)}

    for side in cfg.sides:
        if job["sides"] and side.id not in job["sides"]:
            continue
        t0 = time.monotonic()
        layout = build_layout(project, cfg, side, {**job["texts"], **job["side_texts"][side.id]}, job["north"])
        project.layoutManager().addLayout(layout)
        r = export(layout, f"side-{side.id}", geopdf=True)
        results["sides"].append({"id": side.id, **r, "extent": list(side.extent),
                                 "seconds": round(time.monotonic() - t0, 1)})
        print(f"sheet {side.id}: {r['pdf']} at 1:{r['map_scale']:,.0f} in {results['sides'][-1]['seconds']}s", flush=True)

    ov = job.get("overview")
    if ov and (not job["sides"] or "overview" in job["sides"]):
        t0 = time.monotonic()
        faces = faces_layer(qc, cfg)
        project.addMapLayer(faces, False)          # drawn on the overview only, not in the layer tree
        layers = [faces] + [l for n in ov["layers"] for l in project.mapLayersByName(n)]
        side = Side("overview", job["side_texts"]["overview"]["title"], cfg.aoi)
        layout = build_layout(project, cfg, side, {**job["texts"], **job["side_texts"]["overview"]}, job["north"],
                              overview={"scale": ov["scale"], "layers": layers, "grid_m": ov["grid_m"]})
        layout.setName("Overview")
        project.layoutManager().addLayout(layout)
        r = export(layout, "overview", geopdf=False)
        results["sides"].append({"id": "overview", "overview": True, **r, "extent": list(cfg.aoi),
                                 "seconds": round(time.monotonic() - t0, 1)})
        print(f"overview: {r['pdf']} at 1:{r['map_scale']:,.0f} in {results['sides'][-1]['seconds']}s", flush=True)

    project.write(str(out / "render-project.qgs"))
    Path(job["result"]).write_text(json.dumps(results, indent=2), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)  # skip QGIS teardown


if __name__ == "__main__":
    main()
