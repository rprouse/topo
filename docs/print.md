# Print files and colour

What `make render` produces for the printer and for digital use, and what was found when investigating CMYK output.

## Deliverables

| File | For | Notes |
|---|---|---|
| `build/print/side-A.pdf` … `side-I.pdf`, `overview.pdf` | Printer, one file per face | RGB, exact scale, 300 dpi raster with **lossless** images (no JPEG), text as text with Noto Sans embedded, georeferenced |
| `build/print/sheet.pdf` | Printer, duplex | All ten faces in order, paired front/back: A\|B, C\|D, E\|F, G\|H, I\|overview (five sheets) |
| `build/print/side-A-cmyk.pdf` … | Printer, only if they ask for CMYK | Made when `print.cmyk: true` (see below) |
| `build/digital/side-A.pdf` … | Avenza Maps and other map apps | GeoPDF georeferenced to the map frame, 200 dpi (`digital.dpi`) |
| `build/proofs/…` | Office printer | 100% tiles, greyscale; see the README |

## CMYK: what was found

The brief left open whether the pinned QGIS can export CMYK. It was tested on 2026-11-23:

1. **QGIS 3.44 cannot write CMYK PDFs.** It has a project colour model setting (Project Properties → Styles → Color model: CMYK), but on QGIS 3.44 it has no effect on PDF output. With it set to CMYK, the export is still DeviceRGB, identical to the RGB export. QGIS 3.44 is built on Qt 5.15, whose PDF writer only does RGB. QGIS 4.x is built on Qt 6.10, which can write CMYK, so native CMYK export is expected to work there (not tested here).
2. **Ghostscript can convert the RGB PDF to CMYK** (`print.cmyk: true`). Colours, italic labels and vectors come through, and images stay lossless. About a minute per sheet.
3. **The catch is black.** An RGB→CMYK conversion turns black text and thin black lines (grid, contour labels, road casings) into **rich black**: a mix of all four inks. On press, slight misregistration makes small rich-black text fuzzy. A test conversion of one sheet produced 159 rich-black colour settings and no pure-black ones. Ghostscript's `-dBlackText` / `-dBlackVector` switches were tried. They don't map near-black to pure K; they force *every* text and vector element to black, which destroys the map. They aren't usable.

## Recommendation

- **First (short, digital) run: send the RGB PDFs** (`side-A.pdf` … `side-I.pdf` and `overview.pdf`, or `sheet.pdf`). Digital press workflows convert RGB with the printer's own profile, and usually map neutral black text to pure K. Ask the printer to confirm both: "Will you convert RGB to your CMYK profile, and do you preserve pure black for text and thin lines?"
- **If the printer requires CMYK:** ask for their output ICC profile (e.g. GRACoL 2013, FOGRA51 or their press profile), put it in the repo or another local path, and set:
  ```yaml
  print:
    cmyk: true
    icc_profile: print/press-profile.icc   # path relative to the repo
  ```
  Then tell them about the rich black, and order a hard proof before the run.
- **For offset printing with pure-K black text,** the clean solution is native CMYK from QGIS 4.x, with symbol colours defined in CMYK. Revisit the QGIS version when a 4.x release becomes the long-term release.

Judge colour only on a hard proof from the real press on the real stock, never on screen or the office printer (see `docs/PROCESS.md`, "Pre-press").

## GeoPDF

The digital edition (`build/digital/`) is written with QGIS's GeoPDF writer. The georeferencing covers exactly the map frame (the collar isn't georeferenced) in NAD83(CSRS) / UTM 11N. `digital.include_features: true` also embeds the map features as queryable layers (about 25% larger).

Known quirk: GDAL reports the GeoPDF's neatline with its corners in an unusual order (upper-left, upper-right, lower-left, lower-right). It comes from QGIS's writer. Check the first import into Avenza: the blue position dot should sit in the right place on a known trail junction.

The print PDFs are georeferenced too, but across the whole page, so use the digital edition for apps.
