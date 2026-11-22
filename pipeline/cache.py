"""Download cache under .cache/.

Each fetched source lives in `.cache/sources/<source_id>/<request_key>/`, where
`request_key` is a hash of everything that determines the request (endpoint,
layer, bbox, CRS, snapshot date...). Changing the AOI therefore produces a new
cache entry instead of silently reusing a stale one. Alongside the data sits a
`record.json` describing exactly what was fetched, when, and its checksums.

The cache can be deleted at any time; it only costs download time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

USER_AGENT = "topo-map-pipeline/0.1 (+open-data map build)"
RECORD_NAME = "record.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def stable_hash(obj: Any) -> str:
    """Hash of a JSON-serialisable object, independent of key order."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class CacheEntry:
    """One cached fetch: a directory of files plus record.json."""

    def __init__(self, cache_dir: Path, source_id: str, request: dict[str, Any]):
        self.source_id = source_id
        self.request = request
        self.key = stable_hash(request)[:16]
        self.dir = cache_dir / "sources" / source_id / self.key
        self.record_path = self.dir / RECORD_NAME

    def is_complete(self) -> bool:
        """True if a previous fetch finished and every file still matches its checksum."""
        if not self.record_path.exists():
            return False
        try:
            rec = self.load_record()
        except (OSError, json.JSONDecodeError):
            return False
        for f in rec.get("files", []):
            p = self.dir / f["path"]
            if not p.exists() or p.stat().st_size != f["bytes"]:
                return False
        return True

    def verify_checksums(self) -> list[str]:
        """Return the names of files whose sha256 no longer matches."""
        rec = self.load_record()
        return [f["path"] for f in rec["files"] if sha256_file(self.dir / f["path"]) != f["sha256"]]

    def load_record(self) -> dict[str, Any]:
        return json.loads(self.record_path.read_text(encoding="utf-8"))

    def staging(self) -> Path:
        """A fresh temp dir to download into; promoted by commit()."""
        tmp = self.dir.with_name(self.dir.name + ".partial")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        return tmp

    def commit(self, staging: Path, files: Iterable[str], record: dict[str, Any]) -> dict[str, Any]:
        """Checksum the staged files, write record.json and atomically move into place."""
        entries = []
        for name in sorted(files):
            p = staging / name
            entries.append({"path": name, "bytes": p.stat().st_size, "sha256": sha256_file(p)})
        record = {
            "source_id": self.source_id,
            "request_key": self.key,
            "request": self.request,
            **record,
            "files": entries,
        }
        (staging / RECORD_NAME).write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        if self.dir.exists():
            shutil.rmtree(self.dir)
        os.replace(staging, self.dir)
        return record


def http_session():
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    retry = Retry(total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "HEAD"))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


def download(session, url: str, dest: Path, params: dict | None = None, timeout: int = 300) -> str:
    """Stream a URL to `dest` (atomically). Returns the final URL requested."""
    t0 = time.monotonic()
    tmp = dest.with_name(dest.name + ".tmp")
    with session.get(url, params=params, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for block in r.iter_content(chunk_size=1 << 20):
                f.write(block)
        final_url = r.url
    os.replace(tmp, dest)
    log.debug("downloaded %s (%d bytes, %.1fs)", final_url, dest.stat().st_size, time.monotonic() - t0)
    return final_url
