"""Canadian Geographical Names (CGN) via NRCan's GeoNames API.

CGN is the national database of official names, including British
Columbia's, under the Open Government Licence - Canada. It stands in for BC
Geographical Names, which the BC Data Catalogue lists as "Access Only" (no
reproduction without written permission).

Items intersecting the buffered AOI are paged with `num`/`skip` and saved as
one JSON array sorted by CGN id, so the file is deterministic.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..cache import CacheEntry, http_session, utc_now
from ..config import Config, Source

log = logging.getLogger(__name__)

OUTPUT_NAME = "names.json"
PAGE = 1000


def request_spec(cfg: Config, src: Source) -> dict[str, Any]:
    w, s, e, n = cfg.bbox_in("EPSG:4326", buffered=True)
    return {"kind": "geonames", "endpoint": src.get("endpoint"), "bbox": [round(v, 5) for v in (w, s, e, n)]}


def fetch(cfg: Config, src: Source, entry: CacheEntry) -> dict[str, Any]:
    req = entry.request
    session = http_session()
    started = utc_now()
    items, urls, skip = [], [], 0
    while True:
        r = session.get(req["endpoint"], params={
            "bbox": ",".join(str(v) for v in req["bbox"]), "num": PAGE, "skip": skip}, timeout=120)
        r.raise_for_status()
        urls.append(r.url)
        page = r.json().get("items", [])
        items += page
        if len(page) < PAGE:
            break
        skip += PAGE
    # Drop hypermedia links: they add nothing and vary with the request.
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if k != "links"}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o
    items = sorted((strip(i) for i in items), key=lambda i: i["id"])
    staging = entry.staging()
    (staging / OUTPUT_NAME).write_text(json.dumps(items, indent=1, sort_keys=True, ensure_ascii=False),
                                       encoding="utf-8", newline="\n")
    log.info("  cgn_names: %d names", len(items))
    return entry.commit(staging, [OUTPUT_NAME], {
        "title": src.title, "licence": src.licence, "retrieved_at": started, "urls": urls,
        "feature_count": len(items),
    })
