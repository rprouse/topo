# Fernie Topo

A reproducible pipeline that turns open data into a print-ready 1:25,000 topographic map of the Fernie, BC area.

- [`docs/fernie-topo-pipeline-brief.md`](docs/fernie-topo-pipeline-brief.md): what the pipeline must do
- [`docs/PROCESS.md`](docs/PROCESS.md): the human side: design, proofing, ground truthing, printing

## Status

| Milestone | State |
|---|---|
| 1. Skeleton: Docker image, CLI, config, cache, manifest, fetch | **Done**: BC Freshwater Atlas (streams, lakes, rivers, wetlands) |
| 2. Terrain: DEM selection, contours, hillshade | **Done**: LidarBC, then HRDEM, then CDEM; PNG preview |
| 3. Vectors: OSM, roads, names, corrections → `map.gpkg` | **Done**: OSM (pinned snapshot), Freshwater Atlas, official names, corrections |
| 4. Render: QGIS project, layout, print PDF | **Done**: print PDF per side with grid, collar, attribution, declination |
| 5. Proofs and QA | Not started |
| 6. Polish: GeoPDF, CMYK, double-sided layout, docs | Not started |

## Requirements

Docker and GNU make, nothing else. On Windows, install make (for example `winget install ezwinports.make`) and run it from Git Bash.

## Usage

```sh
make all          # every stage, in order (stops at the first unimplemented stage for now)
make fetch        # download sources into .cache/
make terrain      # DEM composite, contours, hillshade, spot heights, preview
make vectors      # normalise all vector data into build/map.gpkg
make corrections-init  # create an empty authored/corrections.gpkg
make render       # print PDFs and PNG previews per side, through qgis/fernie.qgs
make project-init # generate the starting QGIS project (once; it is hand-edited after)
make manifest     # write build/manifest.json
make config       # validate config and print the resolved AOI, sheet fit and sources
make attribution  # print the generated attribution block
make test         # unit tests, inside the image
make check        # fail if geodata or large binaries are tracked by git
make hooks        # install the pre-commit hook that runs the same check
make shell        # bash inside the build image
```

Pass extra arguments to a single stage with `ARGS`, for example `make fetch ARGS="--only fwa_streams --refresh"`.

Without make, the equivalent is `docker build -t fernie-topo:dev .` followed by `docker run --rm -v "$PWD:/work" fernie-topo:dev <stage>`. From Git Bash, set `MSYS_NO_PATHCONV=1` first.

## How it works

- **Config** (`config/map.yaml`, `config/sources.yaml`) holds everything about the map: CRS, scale, AOI, sheet sides, contour interval, edition, and every data source with its licence and attribution. Change the config, not the code.
- **Environment**: the `Dockerfile` pins the official QGIS 3.44 LTR image by digest (QGIS 3.44.15, GDAL 3.8.4, PROJ 9.4, Python 3.12). Install QGIS desktop 3.44.x for styling and labels so they render the same.
- **Cache**: each source is fetched into `.cache/sources/<id>/<request-key>/`. The key hashes everything that determines the request (endpoint, layer, bbox, CRS), so a changed AOI gets a fresh download instead of stale data. Each entry has a `record.json` with URLs, retrieval time and sha256 checksums. Deleting `.cache/` is always safe.
- **State and manifest**: each stage writes `build/state/<stage>.json` with an input hash, which lets later stages skip unchanged work. `make manifest` assembles these into `build/manifest.json`: sources, URLs, timestamps, checksums, tool versions, git commit, edition and attribution.
- **Attribution** is generated from `sources.yaml`, crediting only the sources the build actually used. Don't hand-edit it.
- **No data in git**: `.cache/` and `build/` are ignored. `scripts/check_no_geodata.py` runs in CI and as an optional pre-commit hook, and fails on rasters, OSM extracts, shapefiles, GeoJSON and similar files, or anything over 5 MB. The authored `corrections.gpkg` and QGIS project files are allow-listed.

## Terrain

The first `make fetch` downloads about 7 GB of LidarBC tiles (roughly 10 minutes), cached in `.cache/terrain/`. Sources are tried in the order given by `terrain.sources` in `map.yaml`, where an entry can be limited to one survey year (`lidarbc_dem:2022`). The current order and why:

1. **LidarBC 2024 and 2022** 1 m bare-earth DEM, about 77% of the AOI. Tiles are found through LidarBC's public ArcGIS tile index and downloaded whole.
2. **NRCan HRDEM** 2 m mosaic, about 23%, mostly the Elk Valley floor. Read remotely (COG) only over the remaining gaps.
3. **NRCan CDEM**, under 0.3%, where no lidar survey exists. CDEM stores whole metres, so it is smoothed before use (`smooth_sigma_m`) to stop the terraces showing in the hillshade. These patches are visibly softer than the lidar around them.

LidarBC's 2016 survey is deliberately left out. Its tiles contain TIN-interpolated fans (long, thin, flat triangles) beyond the real survey coverage, reported as valid data. HRDEM includes the real part of that survey, clipped to its true coverage.

`build/terrain/coverage.png` shows which source supplied each pixel. Check it whenever the AOI changes.

Each raster is resampled onto a shared 2 m grid (`dem_resolution_m`) and cached. A candidate raster is only fetched if it overlaps a remaining gap bigger than `min_fill_area_m2`; smaller voids are interpolated.

`make terrain` writes to `build/terrain/`:

| File | What |
|---|---|
| `dem.tif` | composite DEM, unsmoothed |
| `dem_source.tif`, `coverage.png` | which source and survey year supplied each pixel |
| `dem_smooth.tif` | Gaussian-smoothed DEM (`smoothing_sigma_m`), used only for contours and summits |
| `contours.gpkg` | contours (`elev`, `index`), simplified and Chaikin-smoothed |
| `hillshade.tif` | hillshade from the unsmoothed DEM at `hillshade_resolution_m` |
| `spot_heights.gpkg` | summit candidates; the vectors stage removes named peaks and applies `spot_heights_max` |
| `terrain-report.json` | coverage by source, voids, elevation range, counts |
| `preview.png` | hillshade and contours rendered through QGIS at map scale |

Each terrain step re-runs only when its own inputs change, so tuning the contour settings doesn't rebuild the DEM.

## Vectors

`make vectors` writes `build/map.gpkg` (layers and fields: [`docs/schema.md`](docs/schema.md)) and `build/vectors-report.json`.

- **OpenStreetMap** comes from the Overpass API for the AOI only. `osm.snapshot` in `map.yaml` pins a date, and Overpass returns OSM as it was that day, so rebuilding gives byte-identical data. For a new edition, move the date forward.
- **[`config/osm_mapping.yaml`](config/osm_mapping.yaml)** is the one place OSM tags are interpreted: which features go in which layer, their `class`, and which tags are kept. Change it rather than the code.
- **Water** comes from the BC Freshwater Atlas (official, OGL-BC), not OSM.
- **Official names** come from Canadian Geographical Names. OSM peaks and places keep their OSM names. Where they differ from the official name, or one exists without the other, `vectors-report.json` lists it for you to decide.
- **Spot heights** are DEM summits with no named peak nearby.

### Corrections

`authored/corrections.gpkg` holds fixes that are specific to this map, or that can't go in OSM. It is committed and applied last. Open it in QGIS, or in QField in the field.

- **`edits`** (a table): change a feature by its `source_id` (see the `source_id` field in `map.gpkg`). `action` is `set` (with `field` and `value`, e.g. `difficulty` = `difficult` or `name` = `Lizard Range`), `hide`, `delete` or `close` (`value` is the closure note). Optional `layer` restricts the edit to one layer.
- **`added_points`, `added_lines`, `added_polygons`**: new features. Set `id`, `target_layer` (e.g. `trails`, `pois`), `class` and `name`; other fields go in `attributes` as JSON, e.g. `{"difficulty": "moderate"}`.

An edit whose feature no longer exists in the data (because someone changed OSM) is reported as stale in `vectors-report.json`.

If a stranger mapping the area would want the fix, make it in OSM with JOSM instead; it flows into the next build. Trail difficulty is the main exception: OSM has `sac_scale` on only a few trails here, so grading will mostly live in `edits`.

## Render

`make render` opens `qgis/fernie.qgs` headless and writes, per side of the sheet:
- `build/print/side-A.pdf`, a print PDF at exactly the map scale (300 dpi raster, text as text, georeferenced);
- `side-A.png`, a preview;
- `build/print/render-project.qgs`, the project with the generated layouts, to inspect in QGIS desktop.

The **print layout is generated** from `map.yaml`. The page is the sheet size; the map frame is each side's extent at the map scale, with a title band above and a collar below; the legend is a box in one corner of the map. Changing the scale (1:30,000 works) or a side's extent needs no code changes; if a side won't fit the sheet, the render stops and says by how much. Collar text is generated, never hand-edited:
- **attribution**, from `config/sources.yaml`;
- **declination**, computed from the high-resolution World Magnetic Model (WMMHR-2025, matching NOAA's calculator) for `collar.declination_date`;
- **grid note**;
- **edition and OSM date**;
- **disclaimer**, from `collar.disclaimer` (still a placeholder).

To change the collar design, edit `pipeline/layout.py`.

### Editing styles and labels in QGIS

The **QGIS project** holds the layers, their styles and labels. It was generated once from [`docs/symbol-spec.md`](docs/symbol-spec.md) by `make project-init`, and is now edited by hand:

1. **Install QGIS 3.44.x** (the LTR the Docker image pins) and the **Noto Sans** font family. A different QGIS version or a missing font moves labels.
2. **Run a build** (`make all`, or at least `make terrain vectors`). The project reads `build/` through relative paths.
3. **Open `qgis/fernie.qgs`.** Restyle layers as needed, and update `docs/symbol-spec.md` to match.
4. **Place labels** with the label toolbar (pin, move, rotate, hide). QGIS saves these in `qgis/fernie.qgd`. They are keyed on each feature's `source_id`, so they survive rebuilds; contour labels are the exception.
5. **Save the project and commit `qgis/`**, including `fernie.qgd` and `fernie_attachments.zip`.

`make project-init ARGS=--force` regenerates the project from code and **discards hand edits**. It's only for starting over.

## Data sources and licences

**Not used, for licence reasons:** the BC Digital Road Atlas, Forest Tenure roads and BC Geographical Names are "Access Only" in the BC Data Catalogue: no reproduction without written permission from the Province. Names come from the federal Canadian Geographical Names (OGL-Canada) instead. Roads come from OSM alone. The federal CanVec roads were checked and rejected: in this area they are mostly town streets, with almost no resource roads, dated 1997–2017. If forest service roads turn out to be missing from OSM, either map them in OSM or ask the Province for permission to use the Digital Road Atlas (details in `config/sources.yaml`).

See `config/sources.yaml`. A source's `verified` date records when its endpoint, format and licence were last checked by hand. Unverified sources are skipped. Trailforks data and Google, Esri or Bing imagery must never be added; config loading rejects Trailforks outright.
