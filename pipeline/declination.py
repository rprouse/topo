"""Magnetic declination and grid convergence for the collar.

Declination comes from the high-resolution World Magnetic Model
(WMMHR-2025, valid 2025-2030, public domain) via pygeomag, computed locally
for a configured date, so a rebuild prints the same value. WMMHR includes the
crustal field; around Fernie that moves declination by about 0.4 degrees
compared with the standard WMM. Values match NOAA's online calculator
(WMMHR-2025, sea level). NRCan's online calculator API was the brief's
suggestion, but it only supports dates before 2020.

Grid convergence is the angle between grid north (the UTM grid lines) and
true north at the map centre.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict, dataclass

from .config import Config, transform_point

MODEL = "WMMHR-2025"


@dataclass(frozen=True)
class NorthArrows:
    date: str
    lat: float
    lon: float
    declination_deg: float       # magnetic north relative to true north, east positive
    annual_change_deg: float     # change per year, east positive
    convergence_deg: float       # grid north relative to true north, east positive
    model: str = MODEL

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def magnetic_from_grid_deg(self) -> float:
        """Angle from grid north to magnetic north (what a compass user on the grid needs)."""
        return self.declination_deg - self.convergence_deg


def _decimal_year(d: dt.date) -> float:
    start = dt.date(d.year, 1, 1)
    days = (dt.date(d.year + 1, 1, 1) - start).days
    return d.year + (d - start).days / days


def declination(lat: float, lon: float, date: dt.date, alt_km: float = 0.0) -> float:
    from pygeomag import GeoMag

    model = GeoMag(coefficients_file="wmm/WMMHR_2025.COF", high_resolution=True)
    return float(model.calculate(glat=lat, glon=lon, alt=alt_km, time=_decimal_year(date)).d)


def convergence(cfg: Config, x: float, y: float) -> float:
    """Angle (degrees, east positive) from true north to grid north at (x, y) in the map CRS."""
    lon, lat = transform_point(x, y, cfg.crs, "EPSG:4326")
    x2, y2 = transform_point(lon, lat + 0.01, "EPSG:4326", cfg.crs)   # a step due north
    # Bearing of true north measured in grid coordinates; grid north is the
    # negative of that.
    return -math.degrees(math.atan2(x2 - x, y2 - y))


def compute(cfg: Config) -> NorthArrows:
    date_s = str(cfg.map.get("collar", {}).get("declination_date") or dt.date.today().isoformat())
    date = dt.date.fromisoformat(date_s)
    x = (cfg.aoi[0] + cfg.aoi[2]) / 2
    y = (cfg.aoi[1] + cfg.aoi[3]) / 2
    lon, lat = transform_point(x, y, cfg.crs, "EPSG:4326")
    d0 = declination(lat, lon, date)
    d1 = declination(lat, lon, date.replace(year=date.year + 1))
    return NorthArrows(date=date_s, lat=round(lat, 5), lon=round(lon, 5), declination_deg=round(d0, 2),
                       annual_change_deg=round(d1 - d0, 2), convergence_deg=round(convergence(cfg, x, y), 2))


def describe(n: NorthArrows) -> str:
    """Collar text, e.g. 'Magnetic declination 13.2° E (2027), decreasing 0.1° per year.'"""
    side = "E" if n.declination_deg >= 0 else "W"
    trend = "decreasing" if abs(n.declination_deg + n.annual_change_deg) < abs(n.declination_deg) else "increasing"
    gn = "E" if n.convergence_deg >= 0 else "W"
    return (f"Magnetic declination {abs(n.declination_deg):.1f}° {side} ({n.date[:4]}), "
            f"{trend} about {abs(n.annual_change_deg):.1f}° per year. "
            f"Grid convergence {abs(n.convergence_deg):.1f}° {gn} at map centre. "
            f"Source: World Magnetic Model, high resolution (WMMHR-2025).")
