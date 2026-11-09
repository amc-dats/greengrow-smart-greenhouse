"""Evidence for the sampling-interval decision.

Simulates 14 days of the greenhouse at 1-minute resolution using REAL Cranfield weather (June 2026 scenario),
with no actuators and no sensor noise, as ground truth. It then re-samples the truth at several intervals and
measures what is lost:
  * reconstruction error (RMSE of linear interpolation between samples vs the truth)
  * detection delay of two events that matter to the controller:
      - air temperature rising through 20 C (the heater setpoint) each morning
      - soil moisture falling through the 30 % irrigation trigger
  * messages per day (broker / radio / battery load).

Run (project root, no Python needed on the host):
  docker run --rm -i -v "$PWD:/w:ro" -e PYTHONPATH=/w/backend:/w/common python:3.12-slim python - < scripts/sampling_experiment.py
"""
import math
from dataclasses import replace

from greenhouse import environment as env
from greenhouse import weather as wx

w = wx.WeatherData.load("/w/backend/greenhouse/data/cranfield_summer.json")
DAYS = 14
state = replace(env.initial_state(w.start, w), soil_moisture=65.0)
truth = {"soil_moisture": [], "temperature": [], "humidity": [], "light": []}
for _ in range(DAYS * 24 * 60):
    state = env.advance(state, 1, w)
    s = env.sample(state, w, noise=False)
    for k in truth:
        truth[k].append(s[k])
n = len(truth["temperature"])


def interp(series, every):
    """Linear reconstruction from samples taken every `every` minutes."""
    out = []
    for i in range(n):
        a = (i // every) * every
        b = min(a + every, n - 1)
        out.append(series[a] if b == a else series[a] + (series[b] - series[a]) * (i - a) / (b - a))
    return out


def rmse(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))


def crossings(series, thr, rising):
    return [i for i in range(1, len(series)) if (series[i - 1] < thr <= series[i] if rising else series[i - 1] >= thr > series[i])]


def detection_delay(series, every, thr, rising):
    """Mean delay (min) between the true crossing and the first SAMPLE that shows it."""
    delays = []
    for c in crossings(series, thr, rising):
        j = (c // every + 1) * every if c % every else c
        while j < n and not (series[j] >= thr if rising else series[j] < thr):
            j += every
        if j < n:
            delays.append(j - c)
    return (sum(delays) / len(delays), len(delays)) if delays else (float("nan"), 0)


print(f"{'interval':>9} | {'msgs/day':>8} | {'RMSE temp C':>11} | {'RMSE moist %':>12} | {'RMSE light lux':>14} | {'temp>20C delay min':>18} | {'moisture<30 delay min':>21}")
print("-" * 112)
for every in (1, 5, 15, 30, 60, 120, 240):
    d_t, n_t = detection_delay(truth["temperature"], every, 20.0, True)
    d_m, n_m = detection_delay(truth["soil_moisture"], every, 30.0, False)
    print(f"{every:>6} min | {1440 // every:>8} | {rmse(truth['temperature'], interp(truth['temperature'], every)):>11.3f} | "
          f"{rmse(truth['soil_moisture'], interp(truth['soil_moisture'], every)):>12.3f} | "
          f"{rmse(truth['light'], interp(truth['light'], every)):>14.1f} | {d_t:>11.1f} (n={n_t}) | {d_m:>14.1f} (n={n_m})")
print("\nGround truth: simulated greenhouse (greenhouse/environment.py) driven by real hourly weather, 14 days, 1-minute steps, no actuators.")
