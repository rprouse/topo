# Fernie Topo

A reproducible pipeline that turns open data into a print-ready 1:25,000 topographic map of the Fernie, BC area.

- [`docs/fernie-topo-pipeline-brief.md`](docs/fernie-topo-pipeline-brief.md): what the pipeline must do
- [`docs/PROCESS.md`](docs/PROCESS.md): the human side: design, proofing, ground truthing, printing

## Status

| Milestone | State |
|---|---|
| 1. Skeleton: Docker image, CLI, config, cache, manifest, fetch | **Done**: BC Freshwater Atlas (streams, lakes, rivers, wetlands) |
| 2. Terrain: DEM selection, contours, hillshade | Not started |
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

## Data sources and licences

See `config/sources.yaml`. A source's `verified` date records when its endpoint, format and licence were last checked by hand. Unverified sources are skipped. Trailforks data and Google, Esri or Bing imagery must never be added; config loading rejects Trailforks outright.
