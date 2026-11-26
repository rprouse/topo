# `build/map.gpkg` schema

The vectors stage (`make vectors`) writes every vector layer the map uses into one GeoPackage, in the map CRS (`crs` in `config/map.yaml`) and clipped to the buffered AOI. QGIS styles key on `class`.

## Fields on every layer

| Field | Meaning |
|---|---|
| `source` | `osm`, `fwa` (BC Freshwater Atlas), `cgn` (Canadian Geographical Names), `dem` (derived from the terrain), or `corrections` |
| `source_id` | Stable id: `osm:node/…`, `osm:way/…`, `osm:relation/…`, `fwa:stream/…`, `fwa:lake/…`, `cgn:…`, `dem:spot/x_y`, `corr:…`. The corrections layer refers to features by this id. A polygon mapped into several layers (e.g. a park that is also a protected area) has the same id in each. |
| `class` | Map class, used by the styles |
| `name` | Label text. OSM features keep their OSM name; the official name is in `name_official` where one was matched. |
| `hidden` | 1 if a correction hid it: keep in the data, don't draw |
| `closed`, `closure_note` | Set by a `close` correction |

## Layers

OSM-derived layers come from `config/osm_mapping.yaml`; edit that file to change what goes where. Classes listed are the current ones.

| Layer | Geometry | Source | Classes | Extra fields |
|---|---|---|---|---|
| `roads` | line | OSM | `highway`, `major`, `fsr`, `paved`, `gravel`, `track`, `track_4wd` | `ref`, `surface`, `tracktype`, `access`, `bridge`, `tunnel`, `layer`, `operator` |
| `trails` | line | OSM | `path`, `steps`, `cycleway` | `sac_scale`, `trail_visibility`, `surface`, `mtb_scale`, `mtb_scale_imba`, `access`, `foot`, `bicycle` |
| `railways` | line | OSM | `rail` | |
| `lifts` | line | OSM | the `aerialway` value (`chair_lift`, `t-bar`, …) | |
| `power_lines` | line | OSM | `line`, `minor_line` | |
| `power_towers` | point | OSM | `tower` (`power=tower` and `power=portal`) | |
| `buildings` | polygon | OSM | `building` | |
| `landcover` | polygon | OSM | `forest`, `scrub`, `heath`, `grass`, `rock`, `scree`, `glacier`, `farmland`, `urban`, `industrial`, `quarry`, `cemetery`, `golf`, `park`, `ski_area` | |
| `protected_areas` | polygon | OSM | `national_park`, `protected` | `protect_class`, `operator` |
| `pois` | point | OSM (nodes, and areas as a point on the surface) | `peak`, `saddle`, `viewpoint`, `hut`, `shelter`, `trailhead`, `parking`, `campsite`, `waterfall` | `ele` (metres), `capacity`, `fee`, `operator`, plus the name fields below |
| `places` | point | OSM | the `place` value (`city`, `town`, `village`, `hamlet`, `locality`, …) | `population`, plus the name fields below |
| `water_lines` | line | FWA stream network | `stream`, `intermittent`, `canal` | `stream_order`, `intermittent` |
| `water_areas` | polygon | FWA lakes, rivers, wetlands | `lake`, `river`, `wetland` | `waterbody_type` |
| `names` | point | CGN | the CGN concise code (`MTN`, `RIV`, `LAKE`, `VALL`, `PARK`, `CITY`, `UNP`, …) | `concise`, `generic`, `status` (`official`, `previouslyOfficial`, …), `decision`, `location`, `matched` (1 if paired with an OSM peak or place) |
| `spot_heights` | point | DEM summits | `spot_height` | `elev`, `relief`, `rank` |

### Name fields (`pois`, `places`)

OSM peaks and places are compared with official CGN names (`vectors.name_match` in `map.yaml`): first by matching name, then by proximity. The result is recorded, never applied automatically:

| Field | Meaning |
|---|---|
| `name_osm` | The OSM name |
| `name_official` | The paired official name, if any |
| `official_id` | Its `cgn:` id |
| `name_status` | `match`, `differs`, `unnamed_near_official`, `osm_only` |

Every status other than `match`, and every official name not paired with anything (`official_only`), is listed in `build/vectors-report.json`. Settle each with a `set name` correction if the map should differ from OSM.

### Water

Only real watercourses are drawn from the stream network: FWA `EDGE_TYPE` 1000, 1050, 1100 and 1150 (single-line streams, main and secondary flow) and 2000 and 2300 (canals). The other edge types are construction lines through lakes, double-line rivers and wetlands. `intermittent` comes from FWA feature code GA24850150.

### Spot heights

Summits found in the DEM by the terrain stage, minus any within `vectors.spot_height_exclude_radius_m` of an OSM peak or an official mountain name, capped at `terrain.spot_heights_max`.

## Corrections

`authored/corrections.gpkg` (create it with `make corrections-init`) is applied after everything above. See the docstring in `pipeline/corrections.py` and the README for how to edit it.
