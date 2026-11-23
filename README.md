# Fernie Topo

A reproducible pipeline that turns open data into a print-ready 1:25,000 topographic map of the Fernie, BC area.

- [`docs/fernie-topo-pipeline-brief.md`](docs/fernie-topo-pipeline-brief.md): what the pipeline must do
- [`docs/PROCESS.md`](docs/PROCESS.md): the human side: design, proofing, ground truthing, printing

## Status

| Milestone | State |
|---|---|
| 1. Skeleton: Docker image, CLI, config, cache, manifest, fetch | **Done**: BC Freshwater Atlas (streams, lakes, rivers, wetlands) |
| 2. Terrain: DEM selection, contours, hillshade | **Done**: LidarBC, then HRDEM, then CDEM; PNG preview |
| 3. Vectors: OSM, roads, names, corrections → `map.gpkg` | Not started |
| 4. Render: QGIS project, layout, print PDF | Not started |
| 5. Proofs and QA | Not started |
| 6. Polish: GeoPDF, CMYK, double-sided layout, docs | Not started |

## Requirements

Docker and GNU make, nothing else. On Windows, install make (for example `winget install ezwinports.make`) and run it from Git Bash.

## Usage

```sh
make all          # every stage, in order (stops at the first unimplemented stage for now)
make fetch        # download sources into .cache/
make terrain      # DEM composite, contours, hillshade, spot heights, preview
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

## Data sources and licences

See `config/sources.yaml`. A source's `verified` date records when its endpoint, format and licence were last checked by hand. Unverified sources are skipped. Trailforks data and Google, Esri or Bing imagery must never be added; config loading rejects Trailforks outright.
