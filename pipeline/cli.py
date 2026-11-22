"""Command-line entry point: `python -m pipeline <stage>`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config as config_mod
from .attribution import attribution_text

# Stage name -> milestone that implements it (for stages not built yet).
PENDING = {
    "terrain": "milestone 2",
    "vectors": "milestone 3",
    "render": "milestone 4",
    "proofs": "milestone 5",
    "qa": "milestone 5",
}


def _cmd_config(cfg, args) -> int:
    print(f"root:      {cfg.root}")
    print(f"edition:   {cfg.edition}")
    print(f"crs:       {cfg.crs} ({config_mod.crs_name(cfg.crs)})")
    print(f"scale:     1:{cfg.scale:,}")
    w = cfg.aoi[2] - cfg.aoi[0]
    h = cfg.aoi[3] - cfg.aoi[1]
    print(f"aoi:       {[round(v) for v in cfg.aoi]}  ({w / 1000:.1f} x {h / 1000:.1f} km)")
    print(f"aoi+buf:   {[round(v) for v in cfg.aoi_buffered]}")
    print(f"aoi wgs84: {[round(v, 5) for v in cfg.bbox_in('EPSG:4326', buffered=False)]}")
    sheet = cfg.map["sheet"]
    paper_w = sheet["width_in"] * 0.0254 * cfg.scale / 1000
    paper_h = sheet["height_in"] * 0.0254 * cfg.scale / 1000
    if sheet.get("orientation", "portrait") == "landscape":
        paper_w, paper_h = paper_h, paper_w
    print(f"sheet:     {sheet['width_in']} x {sheet['height_in']} in = {paper_w:.1f} x {paper_h:.1f} km per side at 1:{cfg.scale:,}")
    for s in cfg.sides:
        sw, sh = (s.extent[2] - s.extent[0]) / 1000, (s.extent[3] - s.extent[1]) / 1000
        fits = "fits" if sw <= paper_w and sh <= paper_h else "DOES NOT FIT the sheet (before margins)"
        print(f"side {s.id}:    {s.title!r} {sw:.1f} x {sh:.1f} km, {fits}")
    print("sources:")
    for s in cfg.sources.values():
        print(f"  {s.id:<14} {s.kind:<10} {s.licence:<11} verified={s.verified}")
    return 0


def _cmd_attribution(cfg, args) -> int:
    ids = args.sources or list(cfg.sources)
    print(attribution_text(cfg, ids))
    return 0


def _cmd_fetch(cfg, args) -> int:
    from .stages import fetch

    fetch.run(cfg, only=args.only, refresh=args.refresh, verify=args.verify)
    return 0


def _cmd_manifest(cfg, args) -> int:
    from .stages import manifest

    manifest.run(cfg)
    return 0


def _cmd_pending(stage: str):
    def run(cfg, args) -> int:
        logging.getLogger("pipeline").error("stage '%s' is not implemented yet (%s)", stage, PENDING[stage])
        return 3
    return run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pipeline", description="Fernie topo map build pipeline")
    p.add_argument("--root", default=".", help="repository root (default: .)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("config", help="validate config and print the resolved values")
    sp.set_defaults(func=_cmd_config)

    sp = sub.add_parser("attribution", help="print the generated attribution block")
    sp.add_argument("sources", nargs="*", help="source ids (default: all)")
    sp.set_defaults(func=_cmd_attribution)

    sp = sub.add_parser("fetch", help="download sources into .cache/")
    sp.add_argument("--only", nargs="+", metavar="SOURCE", help="fetch only these source ids")
    sp.add_argument("--refresh", action="store_true", help="ignore the cache and re-download")
    sp.add_argument("--verify", action="store_true", help="re-check sha256 of cached files")
    sp.set_defaults(func=_cmd_fetch)

    for stage in PENDING:
        sub.add_parser(stage, help=f"({PENDING[stage]})").set_defaults(func=_cmd_pending(stage))

    sp = sub.add_parser("manifest", help="write build/manifest.json")
    sp.set_defaults(func=_cmd_manifest)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        cfg = config_mod.load(Path(args.root))
    except (config_mod.ConfigError, FileNotFoundError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
