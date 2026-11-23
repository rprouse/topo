"""Cut a large PDF page into 100%-scale tiles for an office printer.

The source page becomes one Form XObject shared by every tile page, so a
set of 30 tiles is barely bigger than the original and stays vector (sharp
text on a laser printer). Each tile page clips a window of the source into
its printable area, then adds corner crop marks, overlap ticks and a label.
The first page is an index: a thumbnail of the whole sheet with the tile
grid over it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PT_PER_MM = 72 / 25.4
PAPERS_MM = {"letter": (215.9, 279.4), "legal": (215.9, 355.6), "a4": (210.0, 297.0), "tabloid": (279.4, 431.8)}


@dataclass(frozen=True)
class Tile:
    row: int          # 1-based, from the top
    col: int          # 1-based, from the left
    x0: float         # source window, PDF points, origin bottom-left
    y0: float
    w: float
    h: float


@dataclass(frozen=True)
class TilePlan:
    paper: str
    page_w: float     # tile page size, points
    page_h: float
    margin: float
    overlap: float
    rows: int
    cols: int
    tiles: tuple[Tile, ...]


def _count(total: float, window: float, overlap: float) -> int:
    if total <= window:
        return 1
    return math.ceil((total - window) / (window - overlap)) + 1


def plan(src_w: float, src_h: float, paper: str, margin_mm: float, overlap_mm: float) -> TilePlan:
    """Tile a src_w x src_h (points) page, choosing the paper orientation that
    needs fewer pages. Tiles are spread evenly so the overlaps are equal."""
    pw_mm, ph_mm = PAPERS_MM[paper]
    margin, overlap = margin_mm * PT_PER_MM, overlap_mm * PT_PER_MM
    best = None
    for w_mm, h_mm in ((pw_mm, ph_mm), (ph_mm, pw_mm)):
        pw, ph = w_mm * PT_PER_MM, h_mm * PT_PER_MM
        aw, ah = pw - 2 * margin, ph - 2 * margin
        cols, rows = _count(src_w, aw, overlap), _count(src_h, ah, overlap)
        if best is None or rows * cols < best[0]:
            best = (rows * cols, pw, ph, aw, ah, rows, cols)
    _, pw, ph, aw, ah, rows, cols = best
    # Spread tiles so the first starts at the left/top edge and the last ends
    # exactly at the right/bottom edge (overlap is at least `overlap`).
    step_x = (src_w - aw) / (cols - 1) if cols > 1 else 0
    step_y = (src_h - ah) / (rows - 1) if rows > 1 else 0
    tiles = []
    for r in range(rows):
        for c in range(cols):
            x0 = c * step_x
            top = src_h - r * step_y
            tiles.append(Tile(r + 1, c + 1, x0, top - ah, aw, ah))
    return TilePlan(paper, pw, ph, margin, overlap, rows, cols, tuple(tiles))


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_tiles(src_pdf: str, out_pdf: str, p: TilePlan, title: str) -> int:
    """Write the index page plus one page per tile. Returns the page count."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject,
                               NameObject)

    reader = PdfReader(src_pdf)
    src = reader.pages[0]
    sw, sh = float(src.mediabox.width), float(src.mediabox.height)
    writer = PdfWriter()

    form = DecodedStreamObject()
    form.set_data(src.get_contents().get_data())
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(sw), FloatObject(sh)]),
        NameObject("/Resources"): src["/Resources"].get_object().clone(writer),
    })
    form_ref = writer._add_object(form.flate_encode())
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font_ref = writer._add_object(font)

    def page(ops: list[str]):
        pg = writer.add_blank_page(width=p.page_w, height=p.page_h)
        pg[NameObject("/Resources")] = DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Src"): form_ref}),
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        })
        content = DecodedStreamObject()
        content.set_data("\n".join(ops).encode("latin-1", "replace"))
        pg[NameObject("/Contents")] = writer._add_object(content.flate_encode())

    def text(x, y, size, s):
        return f"BT /F1 {_fmt(size)} Tf {_fmt(x)} {_fmt(y)} Td ({_esc(s)}) Tj ET"

    m = p.margin
    # ---- index page: sheet thumbnail with the tile grid
    avail_w, avail_h = p.page_w - 2 * m, p.page_h - 2 * m - 40
    k = min(avail_w / sw, avail_h / sh)
    ox, oy = m + (avail_w - sw * k) / 2, m
    ops = [text(m, p.page_h - m - 12, 11, f"{title} - proof tiles, {p.paper}, {p.rows} x {p.cols} pages"),
           text(m, p.page_h - m - 26, 8, "Print every page at 100% / Actual size (no fit-to-page). "
                                       "Trim at the crop marks on one side of each overlap, tape, and match the grid lines."),
           f"q {_fmt(k)} 0 0 {_fmt(k)} {_fmt(ox)} {_fmt(oy)} cm /Src Do Q",
           "q 0.85 0 0 RG 0.6 w"]
    for t in p.tiles:
        ops.append(f"{_fmt(ox + t.x0 * k)} {_fmt(oy + t.y0 * k)} {_fmt(t.w * k)} {_fmt(t.h * k)} re S")
    ops.append("Q 0.85 0 0 rg")
    for t in p.tiles:
        ops.append(text(ox + (t.x0 + t.w / 2) * k - 8, oy + (t.y0 + t.h / 2) * k - 3, 8, f"{t.row}-{t.col}"))
    page(ops)

    # ---- tiles
    tick = 4 * PT_PER_MM
    for t in p.tiles:
        x, y, w, h = m, m, t.w, t.h
        ops = [f"q {_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)} re W n "
               f"1 0 0 1 {_fmt(x - t.x0)} {_fmt(y - t.y0)} cm /Src Do Q",
               "q 0 G 0.3 w"]
        # corner crop marks in the margin
        for cx, cy, dx, dy in ((x, y, -1, -1), (x + w, y, 1, -1), (x, y + h, -1, 1), (x + w, y + h, 1, 1)):
            ops.append(f"{_fmt(cx + dx * 1)} {_fmt(cy)} m {_fmt(cx + dx * tick)} {_fmt(cy)} l S")
            ops.append(f"{_fmt(cx)} {_fmt(cy + dy * 1)} m {_fmt(cx)} {_fmt(cy + dy * tick)} l S")
        # overlap ticks: where the neighbouring tile's window starts/ends
        step_x = p.tiles[1].x0 - p.tiles[0].x0 if p.cols > 1 else 0
        step_y = (p.tiles[0].y0 - p.tiles[p.cols].y0) if p.rows > 1 else 0
        ov_x, ov_y = w - step_x, h - step_y
        if t.col > 1:   # left neighbour overlaps [x, x + ov_x]
            ops += [f"{_fmt(x + ov_x)} {_fmt(y - 1)} m {_fmt(x + ov_x)} {_fmt(y - tick)} l S",
                    f"{_fmt(x + ov_x)} {_fmt(y + h + 1)} m {_fmt(x + ov_x)} {_fmt(y + h + tick)} l S"]
        if t.row > 1:   # neighbour above overlaps [y + h - ov_y, y + h]
            ops += [f"{_fmt(x - 1)} {_fmt(y + h - ov_y)} m {_fmt(x - tick)} {_fmt(y + h - ov_y)} l S",
                    f"{_fmt(x + w + 1)} {_fmt(y + h - ov_y)} m {_fmt(x + w + tick)} {_fmt(y + h - ov_y)} l S"]
        ops.append("Q 0 g")
        ops.append(text(x + tick + 2, y - 3 * PT_PER_MM - 2, 6.5,
                        f"{title} - tile {t.row}-{t.col} of {p.rows} x {p.cols} ({p.paper}) - print at 100%"))
        page(ops)
    with open(out_pdf, "wb") as f:
        writer.write(f)
    return len(writer.pages)
