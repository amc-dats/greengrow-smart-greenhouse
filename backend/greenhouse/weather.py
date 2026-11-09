"""Outside weather input for the greenhouse simulator.

Real hourly data (temperature, relative humidity, solar radiation) for a location, downloaded once from
Open-Meteo by scripts/fetch_weather.py and cached as JSON in the project, so the stack runs offline.
Weather data by Open-Meteo.com (CC BY 4.0).

Simulated time maps onto the cached period; when the simulation runs past the end of the data, the data
repeats from its start (a documented simplification).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class Outside:
    temperature: float  # C
    humidity: float     # % relative humidity
    solar: float        # W/m2 (shortwave, horizontal)


class WeatherData:
    def __init__(self, start: datetime, temperature: list[float], humidity: list[float], solar: list[float], meta: dict | None = None):
        if not (len(temperature) == len(humidity) == len(solar) >= 2):
            raise ValueError("weather series must have equal length (at least 2 hours)")
        self.start, self.t, self.h, self.s, self.meta = start, temperature, humidity, solar, meta or {}
        self.n = len(temperature)

    @classmethod
    def load(cls, path: str | Path) -> "WeatherData":
        d = json.load(open(path))
        return cls(datetime.fromisoformat(d["start"]), d["temperature_2m"], d["relative_humidity_2m"], d["shortwave_radiation"],
                   {k: d.get(k) for k in ("source", "attribution", "latitude", "longitude", "fetched", "hours")})

    def at(self, when: datetime) -> Outside:
        """Linear interpolation between hourly values; wraps around at the end of the data."""
        pos = ((when - self.start).total_seconds() / 3600.0) % self.n
        i = int(math.floor(pos))
        f = pos - i
        j = (i + 1) % self.n
        lerp = lambda a: a[i] + (a[j] - a[i]) * f  # noqa: E731
        return Outside(lerp(self.t), min(100.0, max(0.0, lerp(self.h))), max(0.0, lerp(self.s)))


class SyntheticWeather:
    """Fallback and test weather: a smooth temperate day (no real location). Used only if no data file is found."""

    def at(self, when: datetime) -> Outside:
        h = when.hour + when.minute / 60
        temp = 8 + 5 * math.sin(2 * math.pi * (h - 9) / 24)
        sun = max(0.0, math.sin(math.pi * (h - 6) / 12)) if 6 <= h <= 18 else 0.0
        return Outside(temp, 80 - 20 * sun, 600 * sun)


def load_weather(path: str | None) -> "WeatherData | SyntheticWeather":
    if path and Path(path).exists():
        return WeatherData.load(path)
    return SyntheticWeather()


_cache: dict = {}


def default_weather():
    """The weather input configured for this deployment (settings.WEATHER_FILE), loaded once per process."""
    from django.conf import settings

    path = settings.WEATHER_FILE
    if path not in _cache:
        _cache[path] = load_weather(path)
    return _cache[path]
