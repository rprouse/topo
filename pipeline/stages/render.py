"""Stage 4: render the print PDFs through the QGIS project.

Opens qgis/fernie.qgs headless, generates a print layout per side (see
pipeline/layout.py) with grid, collar and generated attribution, and exports
build/print/side-<id>.pdf plus PNG previews. Re-runs only when the project,
the map data or the relevant config changed.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import subprocess
import sys

from .. import declination, state
from ..attribution import attribution_lines
from ..cache import sha256_file, stable_hash
from ..config import Config, crs_name
from ..layout import overview_geometry, sheet_geometry

# Layers drawn on the overview face (by layer name in the QGIS project).
OVERVIEW_LAYERS = ["Places", "Official names", "Points of interest", "Roads", "Railways", "Lakes and rivers",
                   "Streams", "Protected areas", "Hillshade", "Landcover"]

log = logging.getLogger(__name__)

STEP_VERSION = 2


def combine_sheet(pdfs: list[str], out: str) -> None:
    """All sides as consecutive pages of one PDF, for duplex printing."""
    from pypdf import PdfWriter

    w = PdfWriter()
    for p in pdfs:
        w.append(p)
    with open(out, "wb") as f:
        w.write(f)


def to_cmyk(src: str, dst: str, icc: str | None) -> None:
    """RGB -> CMYK with Ghostscript. Images stay lossless (no re-encoding to
    JPEG). Black text and lines become 4-colour rich black: see docs/print.md."""
    cmd = ["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite",
           "-sColorConversionStrategy=CMYK", "-dProcessColorModel=/DeviceCMYK",
           "-dAutoFilterColorImages=false", "-sColorImageFilter=FlateEncode",
           "-dAutoFilterGrayImages=false", "-sGrayImageFilter=FlateEncode",
           "-dDownsampleColorImages=false", "-dDownsampleGrayImages=false",
           "-dAutoRotatePages=/None", "-dCompatibilityLevel=1.6"]
    if icc:
        cmd.append(f"-sOutputICCProfile={icc}")
    subprocess.run(cmd + [f"-sOutputFile={dst}", src], check=True)


def collar_texts(cfg: Config, used_sources: list[str], north: declination.NorthArrows, osm_snapshot: str | None) -> dict:
    t = cfg.map.get("terrain", {})
    ci = t.get("contour_interval_m")
    idx = ci * t.get("index_every", 5) if ci else None
    grid_km = float(cfg.map.get("grid", {}).get("spacing_m", 1000)) / 1000
    return {
        "scale_note": (
            f"Scale 1:{cfg.scale:,}. Contour interval {ci:g} m, index contours every {idx:g} m. "
            f"Heights in metres above sea level.\n"
            f"{grid_km:g} km UTM grid, {crs_name(cfg.crs)} ({cfg.crs}); grid labels in km.\n"
            f"{declination.describe(north)}"),
        "attribution": "\n\n".join(attribution_lines(cfg, used_sources)),
        "disclaimer": " ".join(str(cfg.map.get("collar", {}).get("disclaimer", "")).split()),
        "edition": (f"Edition {cfg.edition}\n"
                    + (f"OpenStreetMap data of {osm_snapshot}\n" if osm_snapshot else "")
                    + f"Built {dt.date.today().isoformat()}"),
    }


def run(cfg: Config, sides: list[str] | None = None, force: bool = False) -> dict:
    project = cfg.root / "qgis" / "fernie.qgs"
    if not project.exists():
        raise SystemExit("qgis/fernie.qgs not found. Create it once with `make project-init`.")
    for s in cfg.sides:
        sheet_geometry(cfg, s)          # fail early, with a clear message, if a face doesn't fit
    want_overview = bool(cfg.map.get("sheet", {}).get("overview")) and len(cfg.sides) > 1
    overview = None
    if want_overview:
        ov_scale, _ = overview_geometry(cfg)
        overview = {"scale": ov_scale, "layers": OVERVIEW_LAYERS, "grid_m": 5000}

    fetch = state.read(cfg.build_dir, "fetch") or {}
    used = sorted(fetch.get("sources", {}))
    osm_snapshot = (fetch.get("sources", {}).get("osm") or {}).get("snapshot")
    north = declination.compute(cfg)
    title = cfg.map.get("title", "Map")
    n = len(cfg.sides)
    texts = collar_texts(cfg, used, north, osm_snapshot)
    side_texts = {
        s.id: {"title": s.title or title,
               "subtitle": (f"{title} topographic map, sheet {s.id}" + (f" of {n}" if n > 1 else "")
                            + f"\n1:{cfg.scale:,} · Edition {cfg.edition}")}
        for s in cfg.sides}
    if overview:
        side_texts["overview"] = {
            "title": f"{title}: overview and sheet index",
            "subtitle": (f"{n} sheets at 1:{cfg.scale:,}, outlined and lettered\n"
                         f"Overview 1:{overview['scale']:,} · Edition {cfg.edition}"),
            "scale_note": (
                f"Overview at 1:{overview['scale']:,}: for finding your way around the set, not for navigation. "
                f"Each lettered rectangle is one 1:{cfg.scale:,} map sheet; neighbouring sheets overlap by "
                f"{float(cfg.map['sheet'].get('grid', {}).get('overlap_m', 0)) / 1000:g} km.\n"
                f"5 km UTM grid, {crs_name(cfg.crs)} ({cfg.crs}); grid labels in km.\n"
                f"{declination.describe(north)}")}

    qgd = project.with_suffix(".qgd")
    attach = project.with_name(project.stem + "_attachments.zip")
    inputs = {
        "v": STEP_VERSION,
        "project": sha256_file(project),
        "qgd": sha256_file(qgd) if qgd.exists() else None,
        "attachments": sha256_file(attach) if attach.exists() else None,
        "terrain": (state.read(cfg.build_dir, "terrain") or {}).get("inputs_hash"),
        "vectors": (state.read(cfg.build_dir, "vectors") or {}).get("inputs_hash"),
        "texts": {**texts, "edition": texts["edition"].rsplit("Built", 1)[0]},
        "side_texts": side_texts,
        "config": {k: cfg.map.get(k) for k in ("sheet", "layout", "grid", "scale", "crs", "print", "digital")},
        "sides": [[s.id, list(s.extent)] for s in cfg.sides],
        "code": [sha256_file(cfg.root / "pipeline" / f) for f in ("layout.py", "qgis_render.py")],
    }
    h = stable_hash(inputs)
    out = cfg.build_dir / "print"
    wanted = [s.id for s in cfg.sides if not sides or s.id in sides]
    outputs = [out / f"side-{i}.pdf" for i in wanted] + ([out / "overview.pdf"] if overview and not sides else [])
    if not force and not sides and state.is_current(cfg.build_dir, "render", h, outputs):
        log.info("render: up to date")
        return state.read(cfg.build_dir, "render")

    out.mkdir(parents=True, exist_ok=True)
    job = {
        "root": str(cfg.root), "project": str(project), "out_dir": str(out),
        "digital_dir": str(cfg.build_dir / "digital"),
        "result": str(out / "render-result.json"), "sides": sides or [], "overview": overview,
        "texts": texts, "side_texts": side_texts, "north": north.as_dict(),
    }
    job_path = out / "render-job.json"
    job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("render: %s (this takes a few minutes per side at %s dpi)", ", ".join(wanted),
             cfg.map.get("layout", {}).get("pdf_dpi", 300))
    subprocess.run([sys.executable, "-m", "pipeline.qgis_render", "--job", str(job_path)], check=True)
    result = json.loads((out / "render-result.json").read_text(encoding="utf-8"))
    for s in result["sides"]:
        expected = overview["scale"] if s.get("overview") else cfg.scale
        if abs(s["map_scale"] - expected) > 0.5:
            raise SystemExit(f"sheet {s['id']} rendered at 1:{s['map_scale']}, not 1:{expected}")
    if not sides and len(cfg.sides) > 1:
        # Faces in reading order, then the overview: duplex pairs A|B, C|D, ...
        combine_sheet([str(out / s["pdf"]) for s in result["sides"]], str(out / "sheet.pdf"))
        result["sheet"] = "sheet.pdf"
        log.info("render: sheet.pdf (all sides, for duplex printing)")
    pcfg = cfg.map.get("print", {})
    if pcfg.get("cmyk"):
        icc = pcfg.get("icc_profile")
        icc = str(cfg.root / icc) if icc else None
        for s in result["sides"]:
            log.info("render: side %s CMYK%s", s["id"], f" ({icc})" if icc else " (Ghostscript default profile)")
            stem = s["pdf"][:-4]
            to_cmyk(str(out / s["pdf"]), str(out / f"{stem}-cmyk.pdf"), icc)
            s["cmyk_pdf"] = f"{stem}-cmyk.pdf"
    data = {"result": result, "declination": north.as_dict(), "attribution": texts["attribution"].split("\n\n")}
    if not sides:
        state.write(cfg.build_dir, "render", h, data)
    return data
