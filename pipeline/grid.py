"""The working raster grid every terrain product is aligned to.

The grid covers the buffered AOI in the map CRS, snapped outward to whole
multiples of the resolution, so clips from different sources line up pixel
for pixel and can be composited without resampling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .cache import stable_hash
from .config import Bbox, Config


@dataclass(frozen=True)
class Grid:
    crs: str
    bbox: Bbox
    res: float

    @property
    def width(self) -> int:
        return round((self.bbox[2] - self.bbox[0]) / self.res)

    @property
    def height(self) -> int:
        return round((self.bbox[3] - self.bbox[1]) / self.res)

    @property
    def geotransform(self) -> tuple[float, float, float, float, float, float]:
        return (self.bbox[0], self.res, 0.0, self.bbox[3], 0.0, -self.res)

    @property
    def key(self) -> str:
        return stable_hash({"crs": self.crs, "bbox": self.bbox, "res": self.res})[:12]

    def snap(self, bbox: Bbox) -> Bbox:
        """Snap a bbox outward to this grid and clip it to the grid extent.
        Returns an empty-area bbox if they don't intersect."""
        r = self.res
        x0, y0 = self.bbox[0], self.bbox[1]
        xmin = x0 + math.floor((bbox[0] - x0) / r) * r
        ymin = y0 + math.floor((bbox[1] - y0) / r) * r
        xmax = x0 + math.ceil((bbox[2] - x0) / r) * r
        ymax = y0 + math.ceil((bbox[3] - y0) / r) * r
        out = (max(xmin, self.bbox[0]), max(ymin, self.bbox[1]),
               min(xmax, self.bbox[2]), min(ymax, self.bbox[3]))
        return out

    def window(self, bbox: Bbox) -> tuple[int, int, int, int]:
        """Pixel window (col, row, ncols, nrows) of a grid-aligned bbox."""
        col = round((bbox[0] - self.bbox[0]) / self.res)
        row = round((self.bbox[3] - bbox[3]) / self.res)
        return col, row, round((bbox[2] - bbox[0]) / self.res), round((bbox[3] - bbox[1]) / self.res)


def snap_outward(bbox: Bbox, res: float) -> Bbox:
    return (math.floor(bbox[0] / res) * res, math.floor(bbox[1] / res) * res,
            math.ceil(bbox[2] / res) * res, math.ceil(bbox[3] / res) * res)


def terrain_grid(cfg: Config, res: float | None = None) -> Grid:
    res = float(res or cfg.map["terrain"]["dem_resolution_m"])
    return Grid(cfg.crs, snap_outward(cfg.aoi_buffered, res), res)
