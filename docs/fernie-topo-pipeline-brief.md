# Fernie Topo Map Build Pipeline: Brief for Claude Code

## Purpose

Build a reproducible pipeline that turns open data into a print-ready 1:25,000 topographic map of the Fernie, BC area. The final product is printed on waterproof synthetic paper and may be sold locally, so licensing and attribution must be handled correctly.

One command should run the whole process from an empty checkout: pull the source data, process the terrain, assemble the vector layers, render the map through a QGIS project, and export print PDFs, proof tiles and a georeferenced PDF. Re-running it later, for example for a new edition with fresh OpenStreetMap data, must be a non-event.

## Hard constraints

- **No source data in the repository.** Every dataset is downloaded during the build. Downloads go into a git-ignored cache directory (`.cache/`) so repeated local builds don't re-download. The cache can be deleted at any time without losing anything.
- **Hand-authored content does live in the repo.** This means the QGIS project and styles, the label position overrides (QGIS auxiliary storage), a small corrections layer (see below), the configuration files and the symbol spec. These are authored work, not fetched data.
- **Reproducible environment.** Run everything inside a Docker image based on the official QGIS image, with GDAL and Python versions pinned. The host machine only needs Docker and `make` (or `just`).
- **Config-driven.** The area of interest, scale, sheet size, contour interval, edition label and data sources are all set in config files, not hard-coded.
- **Every build records exactly what it used.** Write a build manifest with the source URLs, retrieval timestamps, file checksums, OSM snapshot date and tool versions.

## Suggested repository layout

```
fernie-topo/
  config/
    map.yaml            # AOI, CRS, scale, sheet size, contour interval, edition
    sources.yaml        # each data source: endpoint, licence, attribution text
  pipeline/             # Python package: fetch, terrain, vectors, render, qa
  qgis/
    fernie.qgz          # QGIS project with print layout(s)
    fernie.qgd          # auxiliary storage (manual label positions)
    styles/*.qml        # per-layer styles
  authored/
    corrections.gpkg    # manual fixes, local names, closures, overrides
  docs/
    symbol-spec.md      # line weights, colours, fonts, minimum sizes
  Dockerfile
  Makefile
  .gitignore            # .cache/, build/
```

## Map parameters (initial values; all in `config/map.yaml`)

| Parameter | Initial value | Notes |
|---|---|---|
| CRS | NAD83(CSRS) / UTM zone 11N (EPSG:2955) | Confirm the EPSG code before using it |
| Scale | 1:25,000 | Must also render at 1:30,000 without changes |
| Sheet | 24 × 36 in, double-sided | Two map faces, configurable |
| AOI | Placeholder: centred near 49.50 N, 115.06 W, roughly 28 × 21 km over both sides | Rob will set the final extents |
| Contours | 20 m, with 100 m index lines | Configurable; 10 m must also work |
| Grid | 1 km UTM grid with edge labels | |
| Edition | e.g. `2027.1` | Printed in the collar and the manifest |

## Data sources

The endpoints below are starting points. **Verify each endpoint, format and licence before writing code against it.** If one has moved or works differently from what's described here, adapt the code and record the change in `sources.yaml`.

### Terrain (ordered by preference)

1. **LidarBC**, the BC government's open lidar portal (https://lidar.gov.bc.ca/), licensed under the Open Government Licence – British Columbia. Use the bare-earth DEM tiles if they cover the AOI. Work out how tiles are indexed and downloaded, and fetch only the tiles that intersect the AOI plus a buffer.
2. **NRCan HRDEM Mosaic**, licensed under the Open Government Licence – Canada. It's available through NRCan's datacube/STAC services, and cloud-optimized GeoTIFFs allow windowed reads, so only the AOI needs to be downloaded.
3. **NRCan CDEM** (about 20 m) as the last resort for any gaps.

Use a coverage check to pick the source automatically, and fill gaps from the next source down. Record which source was used where.

### Vectors

- **OpenStreetMap** (ODbL). Pull the Geofabrik British Columbia extract (`british-columbia-latest.osm.pbf`) and clip it to the AOI, or use the Overpass API for an AOI-only pull. Make the snapshot date pinnable for reproducibility.
  - Features needed: roads by class, tracks, paths and footways (keep the `sac_scale`, `trail_visibility`, `surface`, `mtb:scale` and `access` tags), buildings, landuse/landcover, peaks, saddles, viewpoints, huts and shelters, parking, trailheads, power lines and place names.
- **BC Freshwater Atlas** (OGL-BC): streams, lakes, rivers and wetlands. Pull it through the BC Data Catalogue WFS. The `bcdata` Python package may make this easier.
- **BC Digital Road Atlas** (check the licence): forest service roads, to fill gaps in OSM. Where OSM and DRA overlap, OSM wins unless a correction says otherwise.
- **BC Geographical Names** (BCGNIS/BCGNWS): official place and feature names. These are the primary names source.
- **Magnetic declination**: query NRCan's geomagnetic calculator or API for the map centre at build time, and print the value with the date in the collar.

### Data that must not be used

- **Trailforks** data or exports. Their terms prohibit this use, so don't add it as a source.
- **Google, Esri or Bing imagery or tiles**, except as a manual reference outside the pipeline.

## Pipeline stages

Each stage is a separate CLI command, and `make all` runs them in order. Stages are idempotent and skip work when their inputs haven't changed, judged by hashes of the inputs.

1. **fetch**: download each source into `.cache/` and record URLs, timestamps and checksums.
2. **terrain**:
   - Mosaic and clip the DEM to the AOI plus a buffer, and reproject it to the map CRS.
   - Produce a smoothed DEM for contours (Gaussian or similar, with configurable strength). Raw lidar gives noisy contours.
   - Generate contours with `gdal_contour`, then simplify and smooth the lines. Tag index contours.
   - Generate a hillshade from the unsmoothed DEM, resampled to suit print resolution (about 2–5 m). Make the azimuth and altitude configurable; a multidirectional hillshade is a nice-to-have.
   - Generate spot heights from DEM peaks that have no OSM or BCGNIS peak nearby (optional, and capped in number).
3. **vectors**:
   - Clip and reproject all vector sources, then normalise them into one GeoPackage (`build/map.gpkg`) using a simple, documented schema per layer.
   - Map OSM tags to map classes in a single config-driven table, not scattered code.
   - Apply `authored/corrections.gpkg` last. It can add features, delete or hide features by source ID, override attributes and names, and mark closures.
4. **render**:
   - Open `qgis/fernie.qgz` headless (PyQGIS or `qgis_process`), point it at `build/map.gpkg` and the terrain rasters, and export the print layouts.
   - Outputs: one print PDF per side, a georeferenced PDF (GeoPDF) for digital use, and PNG previews.
   - Investigate whether the pinned QGIS version can export CMYK PDFs. If it can't, add a post-processing step (for example Ghostscript with an ICC profile) and document the trade-off. **Unresolved: needs research.**
5. **proofs**: generate 100%-scale proof tiles on letter and legal paper from the print PDF, with crop marks, overlap and a small index diagram, for printing on an office LaserJet. Also produce a greyscale version.
6. **qa**: write `build/qa-report.md`, containing:
   - which terrain source was used where, and any coverage gaps;
   - a change report against the previous build: trails, roads and names added, removed or reclassified. This is the ground-truthing to-do list, so make it useful (feature IDs, names, and a coordinate or grid reference);
   - features in the corrections layer whose source feature no longer exists (stale corrections);
   - names from OSM that don't match BCGNIS for the same feature;
   - the attribution block generated from `sources.yaml`, so it can be checked against what the layout prints.
7. **manifest**: write `build/manifest.json` with every source, version, checksum and the edition label.

## Attribution and licensing

- Generate the attribution text from `sources.yaml` and inject it into the layout's collar automatically. Don't hand-edit it.
- OSM attribution must credit OpenStreetMap contributors and state that the data is available under the ODbL, with the URL.
- Include the OGL-BC and OGL-Canada attribution statements for every government dataset used.
- Include a liability disclaimer and an edition date in the collar. Rob will supply the wording; use a clearly marked placeholder until then.

## Manual work this pipeline supports but doesn't replace

- **Label positions** are adjusted by hand in QGIS and saved to auxiliary storage (`.qgd`), which is committed. The render stage must use them. Document how to open the project, edit labels, and commit.
- **Corrections from ground truthing** are made in `corrections.gpkg`, either in QGIS or in QField in the field. Where possible, also push upstream fixes to OSM with JOSM. That workflow is outside the pipeline but should be documented in the README.
- **Symbology** lives in the QGIS styles. Write `docs/symbol-spec.md` with the starting values: minimum line weight 0.1–0.15 mm, minimum text 6.5 pt, multiply-blended hillshade, brown contours, blue water, and red/black dashed trails by difficulty class. Styles should follow the spec.

## Milestones

1. **Skeleton**: Docker image, CLI, config loading, cache and manifest. `make fetch` works for one source.
2. **Terrain**: DEM selection and mosaic, contours and hillshade. Output previewed as a PNG.
3. **Vectors**: OSM, freshwater, roads and names into `map.gpkg`, with the tag-mapping table and corrections layer.
4. **Render**: a basic QGIS project and layout, exporting a print PDF from the command line with a grid, collar and attribution.
5. **Proofs and QA**: proof tiles, greyscale version, QA report and change diff.
6. **Polish**: GeoPDF, the CMYK investigation, a double-sided layout, and README documentation of the manual workflows.

Stop at the end of each milestone, summarise what works and what doesn't, and wait for review before starting the next one.

## Acceptance criteria

- `git clone` followed by `make all` on a machine with only Docker produces the print PDFs, proof tiles, GeoPDF, QA report and manifest, without manual steps.
- Deleting `.cache/` and rebuilding with the same pinned OSM snapshot gives the same output, apart from timestamps.
- No downloaded data is committed. CI or a pre-commit check fails if large binary geodata appears in the tree.
- Changing `map.yaml` (scale, contour interval or AOI) and rebuilding works without code changes.

## Open decisions for Rob

These have defaults; flag them as you reach them rather than blocking on them.

- Final AOI and how it splits across the two sides.
- Contour interval: 20 m or 10 m.
- Whether to include an avalanche terrain (ATES) layer. Not in scope until the data licence is confirmed.
- Policy on local, unofficial names, and whether to include Ktunaxa place names.
- Python vs. another language for the CLI. The default is Python because PyQGIS and GDAL bindings make it the practical choice.
