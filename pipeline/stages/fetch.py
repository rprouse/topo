"""Stage 1: fetch every configured source into .cache/."""

from __future__ import annotations

import logging

from .. import sources as fetchers
from .. import state
from ..cache import CacheEntry, stable_hash
from ..config import Config

log = logging.getLogger(__name__)


def _usable(src) -> str | None:
    """Reason a source can't be fetched yet, or None if it can."""
    if src.kind not in fetchers.FETCHERS and src.kind not in fetchers.TERRAIN_KINDS:
        return "not implemented yet: " + fetchers.PLANNED.get(src.kind, f"no fetcher for kind '{src.kind}'")
    if not src.verified:
        return "endpoint not verified (sources.yaml verified: null)"
    return None


def run(cfg: Config, only: list[str] | None = None, refresh: bool = False, verify: bool = False) -> dict:
    selected = only or list(cfg.sources)
    unknown = [s for s in selected if s not in cfg.sources]
    if unknown:
        raise SystemExit(f"Unknown source(s): {', '.join(unknown)}. Known: {', '.join(cfg.sources)}")

    records: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    terrain_ids: list[str] = []
    for sid in selected:
        src = cfg.sources[sid]
        if (why := _usable(src)) is not None:
            skipped[sid] = why
            log.warning("skip %-14s %s", sid, why)
            continue
        if src.kind in fetchers.TERRAIN_KINDS:
            terrain_ids.append(sid)
            continue

        mod = fetchers.get(src.kind)
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

    prev = state.read(cfg.build_dir, "fetch") or {}
    terrain_selection = prev.get("terrain")
    if terrain_ids:
        from ..sources.terrain import fetch_terrain, parse_priority

        entries = [e for e in cfg.map["terrain"].get("sources", [])
                   if parse_priority([e])[0][0] in terrain_ids]
        mentioned = {sid for sid, _ in parse_priority(entries)}
        entries += [s for s in terrain_ids if s not in mentioned]
        trecs, terrain_selection = fetch_terrain(cfg, entries, refresh=refresh, verify=verify)
        records.update(trecs)
        # A lower-priority source that turned out not to be needed is not
        # part of this build; drop any stale record of it.
        for sid in terrain_ids:
            if sid not in trecs:
                prev.get("sources", {}).pop(sid, None)

    # Merge with previously recorded sources when fetching a subset, so
    # `fetch --only x` doesn't drop the others from the build record.
    prev_sources = prev.get("sources", {}) if only else {}
    merged = {**{k: v for k, v in prev_sources.items() if k in cfg.sources}, **records}
    if not only:
        merged = records
    state.write(cfg.build_dir, "fetch",
                stable_hash({k: v["request_key"] for k, v in merged.items()} | {"terrain": terrain_selection}),
                {"sources": merged, "skipped": skipped, "terrain": terrain_selection})
    return merged
