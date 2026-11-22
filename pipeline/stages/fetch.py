"""Stage 1: fetch every configured source into .cache/."""

from __future__ import annotations

import logging

from .. import sources as fetchers
from .. import state
from ..cache import CacheEntry, stable_hash
from ..config import Config

log = logging.getLogger(__name__)


def run(cfg: Config, only: list[str] | None = None, refresh: bool = False, verify: bool = False) -> dict:
    selected = only or list(cfg.sources)
    unknown = [s for s in selected if s not in cfg.sources]
    if unknown:
        raise SystemExit(f"Unknown source(s): {', '.join(unknown)}. Known: {', '.join(cfg.sources)}")

    records: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    for sid in selected:
        src = cfg.sources[sid]
        mod = fetchers.get(src.kind)
        if mod is None:
            why = fetchers.PLANNED.get(src.kind, f"no fetcher for kind '{src.kind}'")
            skipped[sid] = f"not implemented yet: {why}"
            log.warning("skip %-14s %s", sid, skipped[sid])
            continue
        if not src.verified:
            skipped[sid] = "endpoint not verified (sources.yaml verified: null)"
            log.warning("skip %-14s %s", sid, skipped[sid])
            continue

        entry = CacheEntry(cfg.cache_dir, sid, mod.request_spec(cfg, src))
        if entry.is_complete() and not refresh:
            rec = entry.load_record()
            if verify and (bad := entry.verify_checksums()):
                log.warning("  %s: checksum mismatch in %s, re-fetching", sid, bad)
            else:
                log.info("cached %-14s %s (retrieved %s)", sid, entry.key, rec["retrieved_at"])
                records[sid] = rec
                continue
        log.info("fetch  %-14s -> .cache/sources/%s/%s", sid, sid, entry.key)
        records[sid] = mod.fetch(cfg, src, entry)
        log.info("  done %s: %s features", sid, records[sid].get("feature_count", "?"))

    # Merge with any previously recorded sources when fetching a subset, so
    # `fetch --only x` doesn't drop the others from the build record.
    prev = (state.read(cfg.build_dir, "fetch") or {}).get("sources", {}) if only else {}
    merged = {**{k: v for k, v in prev.items() if k in cfg.sources}, **records}
    state.write(cfg.build_dir, "fetch", stable_hash({k: v["request_key"] for k, v in merged.items()}),
                {"sources": merged, "skipped": skipped})
    return merged
