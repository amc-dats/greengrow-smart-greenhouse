"""Sunrise and sunset from the date and a location (NOAA approximation, accurate to a few minutes). Pure Python, offline."""
from __future__ import annotations

import math
from datetime import date


def sun_times_utc(d: date, lat: float, lon: float) -> tuple[float, float] | None:
    """(sunrise, sunset) as decimal hours UTC, or None in polar day/night. West longitudes are negative."""
    n = d.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    cos_ha = math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(decl)) - math.tan(la) * math.tan(decl)
    if not -1 <= cos_ha <= 1:
        return None
    ha = math.degrees(math.acos(cos_ha))
    noon = 720 - 4 * lon - eqtime
    return (noon - 4 * ha) / 60, (noon + 4 * ha) / 60
