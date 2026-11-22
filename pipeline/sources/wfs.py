"""OGC WFS 2.0 fetcher (used for the BC Data Catalogue / openmaps.gov.bc.ca).

Requests only the features intersecting the buffered AOI, in the source's
native CRS, paging with startIndex + a stable sortBy so the result is
complete and deterministic. Pages are merged into a single GeoJSON file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..cache import CacheEntry, download, http_session, utc_now
from ..config import Config, Source

log = logging.getLogger(__name__)

OUTPUT_NAME = "data.geojson"


def request_spec(cfg: Config, src: Source) -> dict[str, Any]:
    """Everything that determines what this fetch returns (used as the cache key)."""
    native = src.get("native_crs", "EPSG:4326")
    bbox = cfg.bbox_in(native, buffered=True)
    # Round outward to whole metres/micro-degrees so the key is stable.
    nd = 6 if native == "EPSG:4326" else 0
    bbox = (
        round(bbox[0] - 0.5 * 10 ** -nd, nd), round(bbox[1] - 0.5 * 10 ** -nd, nd),
        round(bbox[2] + 0.5 * 10 ** -nd, nd), round(bbox[3] + 0.5 * 10 ** -nd, nd),
    )
    return {
        "kind": "wfs",
        "endpoint": src.get("endpoint"),
        "layer": src.get("layer"),
        "crs": native,
        "bbox": list(bbox),
        "sort_by": src.get("sort_by", "OBJECTID"),
    }


def fetch(cfg: Config, src: Source, entry: CacheEntry) -> dict[str, Any]:
    req = entry.request
    page_size = int(src.get("page_size", 10000))
    crs_urn = "urn:ogc:def:crs:" + req["crs"].replace(":", "::")
    base = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": req["layer"],
        "outputFormat": "application/json",
        "srsName": req["crs"],
        "bbox": ",".join(str(v) for v in req["bbox"]) + "," + crs_urn,
        "sortBy": req["sort_by"],
        "count": page_size,
    }

    staging = entry.staging()
    session = http_session()
    urls: list[str] = []
    features: list[dict] = []
    crs_member = None
    started = utc_now()
    start = 0
    page = 0
    while True:
        page_path = staging / f"page-{page:04d}.json"
        urls.append(download(session, req["endpoint"], page_path, params={**base, "startIndex": start}))
        fc = json.loads(page_path.read_text(encoding="utf-8"))
        page_path.unlink()
        if "features" not in fc:
            raise RuntimeError(f"{src.id}: unexpected WFS response: {str(fc)[:300]}")
        crs_member = crs_member or fc.get("crs")
        features.extend(fc["features"])
        log.info("  %s: page %d, %d features (total %d)", src.id, page + 1, len(fc["features"]), len(features))
        if len(fc["features"]) < page_size:
            break
        start += page_size
        page += 1

    out: dict[str, Any] = {"type": "FeatureCollection"}
    if crs_member:
        out["crs"] = crs_member
    out["features"] = features
    (staging / OUTPUT_NAME).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")

    return entry.commit(staging, [OUTPUT_NAME], {
        "title": src.title,
        "licence": src.licence,
        "retrieved_at": started,
        "urls": urls,
        "feature_count": len(features),
    })
