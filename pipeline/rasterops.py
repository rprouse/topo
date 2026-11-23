"""Small numpy raster operations shared by the fetch and terrain stages."""

from __future__ import annotations

import math

import numpy as np

NODATA = -32767.0


def gaussian_smooth(dem: np.ndarray, sigma_px: float, nodata: float = NODATA, strip: int = 2048) -> np.ndarray:
    """Nodata-aware Gaussian smoothing (normalised convolution), in strips to bound memory.

    Nodata pixels stay nodata and don't pull their neighbours towards zero.
    """
    from scipy import ndimage

    if sigma_px <= 0:
        return dem.copy()
    halo = int(math.ceil(3 * sigma_px)) + 1
    out = np.full_like(dem, nodata)
    for r0 in range(0, dem.shape[0], strip):
        a, b = max(0, r0 - halo), min(dem.shape[0], r0 + strip + halo)
        block = dem[a:b]
        valid = block != nodata
        num = ndimage.gaussian_filter(np.where(valid, block, 0).astype(np.float32), sigma_px, mode="nearest", truncate=3.0)
        den = ndimage.gaussian_filter(valid.astype(np.float32), sigma_px, mode="nearest", truncate=3.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            sm = np.where(valid & (den > 1e-6), num / np.maximum(den, 1e-6), nodata).astype(np.float32)
        r1 = min(dem.shape[0], r0 + strip)
        out[r0:r1] = sm[r0 - a:r0 - a + (r1 - r0)]
    return out
