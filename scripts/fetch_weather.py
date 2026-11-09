"""Download hourly weather once from Open-Meteo (historical archive) and cache it as JSON in the project,
so the simulator runs offline and reproducibly afterwards.

Data: Open-Meteo.com archive API (https://open-meteo.com), licence CC BY 4.0 - attribution required.
Only the coordinates and dates are sent to the service.

Usage (no Python needed on the host; run in a container from the project root):
  docker run --rm -i -v "$PWD:/w" python:3.12-slim python - 52.07 -0.63 2025-12-01 2026-02-28 --coldest-days 14 --out /w/backend/greenhouse/data/cranfield_winter.json < scripts/fetch_weather.py
"""
import argparse
import json
import urllib.request
from datetime import date, datetime, timedelta, timezone

p = argparse.ArgumentParser()
p.add_argument("lat", type=float)
p.add_argument("lon", type=float)
p.add_argument("start")
p.add_argument("end")
p.add_argument("--coldest-days", type=int, default=0, help="keep only the coldest N-day window (by mean temperature)")
p.add_argument("--out", required=True)
a = p.parse_args()

url = (
    "https://archive-api.open-meteo.com/v1/archive"
    f"?latitude={a.lat}&longitude={a.lon}&start_date={a.start}&end_date={a.end}"
    "&hourly=temperature_2m,relative_humidity_2m,shortwave_radiation&timezone=UTC"
)
raw = json.load(urllib.request.urlopen(url, timeout=60))
h = raw["hourly"]
times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in h["time"]]
cols = {k: h[k] for k in ("temperature_2m", "relative_humidity_2m", "shortwave_radiation")}
n = len(times)
if any(v is None for c in cols.values() for v in c):
    raise SystemExit("the archive returned missing values; pick a different date range")

lo, hi = 0, n
if a.coldest_days:
    w = a.coldest_days * 24
    best = min(range(0, n - w + 1, 24), key=lambda i: sum(cols["temperature_2m"][i:i + w]) / w)  # start at midnight
    lo, hi = best, best + w

out = {
    "source": "Open-Meteo.com archive API",
    "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)",
    "latitude": raw["latitude"], "longitude": raw["longitude"], "elevation_m": raw.get("elevation"),
    "fetched": date.today().isoformat(),
    "start": times[lo].isoformat(),
    "hours": hi - lo,
    "temperature_2m": cols["temperature_2m"][lo:hi],
    "relative_humidity_2m": cols["relative_humidity_2m"][lo:hi],
    "shortwave_radiation": cols["shortwave_radiation"][lo:hi],
}
json.dump(out, open(a.out, "w"))
t = out["temperature_2m"]
print(f"saved {a.out}: {out['hours']} hours from {out['start']}")
print(f"temperature min {min(t):.1f} C, mean {sum(t) / len(t):.1f} C, max {max(t):.1f} C; hours below 0 C: {sum(1 for x in t if x < 0)}")
print(f"humidity mean {sum(out['relative_humidity_2m']) / len(t):.0f} %; peak solar {max(out['shortwave_radiation']):.0f} W/m2")
