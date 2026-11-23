"""Stage 5a: proof tiles for an office printer.

For each side: build/proofs/side-<id>-<paper>.pdf (index page + 100%-scale
tiles with crop marks and overlap), and with `proofs.greyscale` a greyscale
copy of the print PDF (side-<id>-grey.pdf) tiled the same way.
"""

from __future__ import annotations

import logging
import subprocess

from .. import state
from ..cache import sha256_file, stable_hash
from ..config import Config
from ..pdftile import plan, write_tiles

log = logging.getLogger(__name__)


def greyscale(src: str, dst: str) -> None:
    subprocess.run(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pdfwrite",
                    "-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray",
                    "-dAutoRotatePages=/None", "-dCompatibilityLevel=1.6", f"-sOutputFile={dst}", src], check=True)


def run(cfg: Config, force: bool = False) -> dict:
    render = state.read(cfg.build_dir, "render")
    if not render:
        raise SystemExit("No print PDFs yet. Run `make render` first.")
    pcfg = cfg.map.get("proofs", {})
    papers = pcfg.get("papers", ["letter"])
    margin, overlap = float(pcfg.get("margin_mm", 8)), float(pcfg.get("overlap_mm", 12))
    grey = bool(pcfg.get("greyscale", True))
    src_dir, out = cfg.build_dir / "print", cfg.build_dir / "proofs"
    # (id, file stem): side-A ... side-I, and overview when there is one.
    sides = [(s["id"], s["pdf"][:-4]) for s in render["result"]["sides"]]
    h = stable_hash({"render": render["inputs_hash"], "proofs": pcfg, "v": 1,
                     "code": sha256_file(cfg.root / "pipeline" / "pdftile.py")})
    outputs = [out / f"{stem}-{p}.pdf" for _, stem in sides for p in papers]
    if not force and state.is_current(cfg.build_dir, "proofs", h, outputs):
        log.info("proofs: up to date")
        return state.read(cfg.build_dir, "proofs")

    out.mkdir(parents=True, exist_ok=True)
    title = cfg.map.get("title", "Map")
    sets = []
    for sid, stem in sides:
        variants = [("", str(src_dir / f"{stem}.pdf"))]
        if grey:
            gpdf = out / f"{stem}-grey.pdf"
            log.info("proofs: side %s greyscale", sid)
            greyscale(variants[0][1], str(gpdf))
            variants.append(("-grey", str(gpdf)))
        for paper in papers:
            for suffix, src in variants:
                from pypdf import PdfReader

                box = PdfReader(src).pages[0].mediabox
                tp = plan(float(box.width), float(box.height), paper, margin, overlap)
                dst = out / f"{stem}-{paper}{suffix}.pdf"
                n = write_tiles(src, str(dst), tp,
                                f"{title} {cfg.edition}, " + ("overview" if sid == "overview" else f"sheet {sid}")
                                + (" (greyscale)" if suffix else ""))
                sets.append({"side": sid, "paper": paper, "greyscale": bool(suffix), "file": dst.name,
                             "rows": tp.rows, "cols": tp.cols, "pages": n})
                log.info("proofs: %s: %d x %d tiles (+ index)", dst.name, tp.rows, tp.cols)
    state.write(cfg.build_dir, "proofs", h, {"sets": sets})
    return {"sets": sets}
