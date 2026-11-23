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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    job = json.loads(Path(ap.parse_args().job).read_text(encoding="utf-8"))
    root = Path(job["root"])
    sys.path.insert(0, str(root))

    import qgis.core as qc

    from pipeline import config
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
    results = {"qgis": qc.Qgis.version(), "sides": []}
    for side in cfg.sides:
        if job["sides"] and side.id not in job["sides"]:
            continue
        t0 = time.monotonic()
        texts = {**job["texts"], **job["side_texts"][side.id]}
        layout = build_layout(project, cfg, side, texts, job["north"])
        project.layoutManager().addLayout(layout)
        exporter = qc.QgsLayoutExporter(layout)

        pdf = out / f"side-{side.id}.pdf"
        ps = qc.QgsLayoutExporter.PdfExportSettings()
        ps.dpi = float(lay.get("pdf_dpi", 300))
        ps.rasterizeWholeImage = False
        ps.forceVectorOutput = False
        ps.appendGeoreference = True
        ps.exportMetadata = True
        ps.simplifyGeometries = False
        ps.textRenderFormat = qc.Qgis.TextRenderFormat.AlwaysText
        res = exporter.exportToPdf(str(pdf), ps)
        if res != qc.QgsLayoutExporter.Success:
            raise SystemExit(f"PDF export failed for side {side.id}: {exporter.errorMessage()}")

        png = out / f"side-{side.id}.png"
        # Preview: render the page straight to an image (exportToImage would try
        # to write georeferencing into the PNG, which GDAL can't).
        img = exporter.renderPageToImage(0, dpi=float(lay.get("preview_dpi", 50)))
        if not img.save(str(png), "PNG"):
            raise SystemExit(f"PNG preview failed for side {side.id}")

        m = layout.itemById("map")
        results["sides"].append({
            "id": side.id, "pdf": pdf.name, "png": png.name,
            "map_scale": round(m.scale(), 3), "extent": list(side.extent),
            "seconds": round(time.monotonic() - t0, 1),
        })
        print(f"side {side.id}: {pdf.name} at 1:{m.scale():,.0f} in {results['sides'][-1]['seconds']}s", flush=True)

    project.write(str(out / "render-project.qgs"))
    Path(job["result"]).write_text(json.dumps(results, indent=2), encoding="utf-8")
    sys.stdout.flush()
    os._exit(0)  # skip QGIS teardown


if __name__ == "__main__":
    main()
