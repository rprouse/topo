"""OpenStreetMap via the Overpass API, for the buffered AOI only.

The snapshot is pinnable: with `osm.snapshot: YYYY-MM-DD` in map.yaml the
query carries `[date:...]`, and Overpass answers from its history as of that
moment, so the same date always gives the same data. With `latest`, the
current database is used and the cache entry is keyed by the fetch date, so
it refreshes at most once a day.

The query returns every way in the bbox, multipolygon / protected-area /
trail-route relations touching it, and tagged nodes, with full recursion so
that ways and multipolygons crossing the edge are complete (the vectors stage
clips them). The output is OSM XML, read with GDAL's OSM driver.

The `<meta osm_base=...>` line (when the query ran) is moved into the fetch
record so the file's checksum depends only on the data.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any

from ..cache import CacheEntry, http_session, utc_now
from ..config import Config, Source

log = logging.getLogger(__name__)

OUTPUT_NAME = "data.osm"

QUERY = """[out:xml][timeout:{timeout}][bbox:{s},{w},{n},{e}]{date};
(
  way;
  relation[type=multipolygon];
  relation[type=boundary][boundary=protected_area];
  relation[type=route][route~"^({routes})$"];
  node[~"."~"."];
)->.a;
(.a; .a >;);
out meta;
"""


def snapshot(cfg: Config) -> str:
    snap = str((cfg.map.get("osm") or {}).get("snapshot", "latest")).strip()
    if snap != "latest":
        try:
            dt.date.fromisoformat(snap)
        except ValueError:
            raise ValueError(f"map.yaml osm.snapshot must be 'latest' or YYYY-MM-DD, got {snap!r}") from None
    return snap


def request_spec(cfg: Config, src: Source) -> dict[str, Any]:
    w, s, e, n = cfg.bbox_in("EPSG:4326", buffered=True)
    snap = snapshot(cfg)
    spec = {
        "kind": "overpass",
        "endpoint": src.get("endpoint"),
        "bbox": [round(v, 5) for v in (s, w, n, e)],
        "snapshot": snap,
        "routes": src.get("route_types", ["hiking", "foot", "mtb", "bicycle", "ski", "piste"]),
    }
    if snap == "latest":
        spec["fetched_on"] = dt.date.today().isoformat()
    return spec


def build_query(req: dict, timeout: int = 600) -> str:
    s, w, n, e = req["bbox"]
    date = f'[date:"{req["snapshot"]}T00:00:00Z"]' if req["snapshot"] != "latest" else ""
    return QUERY.format(timeout=timeout, s=s, w=w, n=n, e=e, date=date, routes="|".join(req["routes"]))


def fetch(cfg: Config, src: Source, entry: CacheEntry) -> dict[str, Any]:
    req = entry.request
    query = build_query(req)
    staging = entry.staging()
    started = utc_now()
    session = http_session()
    r = session.post(req["endpoint"], data={"data": query}, timeout=900)
    r.raise_for_status()
    text = r.text
    if "<osm" not in text[:500]:
        raise RuntimeError(f"osm: unexpected Overpass response: {text[:300]}")
    if "<remark>" in text[-2000:]:
        # Overpass reports timeouts / memory errors as a remark at the end of an otherwise valid file.
        raise RuntimeError("osm: Overpass returned an incomplete result: "
                           + re.search(r"<remark>(.*?)</remark>", text, re.S).group(1).strip())
    m = re.search(r'\s*<meta osm_base="([^"]+)"[^>]*/>\n?', text)
    osm_base = m.group(1) if m else None
    if m:
        text = text[:m.start()] + "\n" + text[m.end():]
    (staging / OUTPUT_NAME).write_text(text, encoding="utf-8", newline="\n")
    log.info("  osm: %.1f MB, snapshot %s, database as of %s", len(text) / 1e6, req["snapshot"], osm_base)
    return entry.commit(staging, [OUTPUT_NAME], {
        "title": src.title,
        "licence": src.licence,
        "retrieved_at": started,
        "urls": [req["endpoint"]],
        "query": query,
        "osm_base": osm_base,
        "snapshot": req["snapshot"],
        "feature_count": text.count("<way ") + text.count("<relation "),
    })
