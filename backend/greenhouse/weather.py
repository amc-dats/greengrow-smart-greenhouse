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
import random
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


class ClimatologyWeather:
    """Weather beyond the end of the real data: each new day is a real day drawn from the same time of year.

    Within the cached period it returns the real values. After it, each calendar day takes the hourly profile of a day picked at
    random (seeded by the date, so a run is repeatable) from the real days within +/- WINDOW_DAYS of the same day of the year,
    which is the data's own monthly distribution of temperature, humidity and sunshine, including its cold snaps and dull spells.
    Days are not forecasts: they are plausible weather for the season. The profile of one day joins the next across midnight by
    interpolating between the hours, so there can be a step of a few degrees there."""

    WINDOW_DAYS = 15

    def __init__(self, history: WeatherData, seed: int = 1):
        self.history, self.seed = history, seed
        self.start, self.meta, self.n = history.start, history.meta, history.n
        self.days = []                                   # (day of year, first hour index) for every complete real day
        for i in range(0, history.n - 23, 24):
            self.days.append(((history.start + timedelta(hours=i)).timetuple().tm_yday, i))
        if not self.days:
            raise ValueError("climatology weather needs at least one full day of real data")

    def _source_day(self, day) -> int:
        """Index of the first hour of the real day chosen for this calendar day."""
        doy = day.timetuple().tm_yday
        near = [i for d, i in self.days if min(abs(d - doy), 366 - abs(d - doy)) <= self.WINDOW_DAYS] or [i for _, i in self.days]
        return random.Random(f"{self.seed}-{day.isoformat()}").choice(near)

    def _hour(self, when: datetime) -> tuple[float, float, float]:
        """The (temperature, humidity, solar) of the whole hour starting at `when` (minutes ignored)."""
        h = (when - self.start).total_seconds() / 3600.0
        if h < self.n:
            i = int(math.floor(h)) % self.n
            return self.history.t[i], self.history.h[i], self.history.s[i]
        i = self._source_day(when.date()) + when.hour
        return self.history.t[i], self.history.h[i], self.history.s[i]

    def at(self, when: datetime) -> Outside:
        if (when - self.start).total_seconds() / 3600.0 < self.n - 1:
            return self.history.at(when)
        base = when.replace(minute=0, second=0, microsecond=0)
        f = (when - base).total_seconds() / 3600.0
        a, b = self._hour(base), self._hour(base + timedelta(hours=1))
        lerp = lambda x, y: x + (y - x) * f  # noqa: E731
        return Outside(lerp(a[0], b[0]), min(100.0, max(0.0, lerp(a[1], b[1]))), max(0.0, lerp(a[2], b[2])))


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
    mode = getattr(settings, "FUTURE_WEATHER", "wrap")
    if (path, mode) not in _cache:
        w = load_weather(path)
        if mode == "climatology" and isinstance(w, WeatherData):
            w = ClimatologyWeather(w, seed=getattr(settings, "FUTURE_WEATHER_SEED", 1))
        _cache[(path, mode)] = w
    return _cache[(path, mode)]
