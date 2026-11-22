"""Generate the collar attribution block from sources.yaml.

Only sources actually used in the build are credited (pass their ids); each
licence's statement appears once, followed by the credits under it.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Iterable

from .config import Config

# Order licences appear in the collar.
LICENCE_ORDER = ("odbl", "ogl-bc", "ogl-canada")


def attribution_lines(cfg: Config, source_ids: Iterable[str]) -> list[str]:
    by_licence: dict[str, OrderedDict[str, None]] = {}
    for sid in source_ids:
        src = cfg.sources[sid]
        by_licence.setdefault(src.licence, OrderedDict())[f"{src.title} ({src.credit})" if src.credit else src.title] = None

    order = [l for l in LICENCE_ORDER if l in by_licence] + sorted(set(by_licence) - set(LICENCE_ORDER))
    lines = []
    for lid in order:
        lic = cfg.licences[lid]
        lines.append(f"{lic.attribution} Sources: {'; '.join(by_licence[lid])}. Licence: {lic.url}")
    return lines


def attribution_text(cfg: Config, source_ids: Iterable[str]) -> str:
    return "\n".join(attribution_lines(cfg, source_ids))
