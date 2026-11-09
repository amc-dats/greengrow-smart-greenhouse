"""Closed-loop realism report: runs the simulator + rules-based controller on a weather scenario and prints
per-day temperature/humidity statistics and a diurnal profile.

  docker run --rm -i -v "$PWD:/w:ro" -e PYTHONPATH=/w/backend:/w/common -e DJANGO_SECRET_KEY=x \
      python:3.12-slim python - winter < scripts/closed_loop_report.py        # or: summer
"""
import statistics as st
import sys
from datetime import timedelta

from greenhouse import closedloop, weather as wx

scenario = sys.argv[1] if len(sys.argv) > 1 else "winter"
w = wx.WeatherData.load(f"/w/backend/greenhouse/data/cranfield_{scenario}.json")
res = closedloop.run(w, w.start, days=14)
rows = res["rows"]

print(f"scenario: {scenario} ({w.meta.get('latitude')}, {w.meta.get('longitude')}), starts {w.start:%Y-%m-%d}")
print(f"{'day':>10} | {'out min/max':>11} | {'gh min':>6} {'gh max':>6} | {'RH min':>6} {'RH max':>6} {'RH day':>6} | {'heater h':>8} | {'mist h':>6} | {'vent open h':>11}")
for d in range(14):
    day = [r for r in rows if r["t"] >= w.start + timedelta(days=d) and r["t"] < w.start + timedelta(days=d + 1)]
    heater_h = sum(1 for r in day if r["heater_s"]) * 0.5
    mist_h = sum(1 for r in day if r["mist_s"]) * 0.5
    daytime = [r["rh"] for r in day if 7 <= r["t"].hour < 17]
    print(f"{(w.start + timedelta(days=d)):%Y-%m-%d} | {min(r['out_temp'] for r in day):5.1f}/{max(r['out_temp'] for r in day):<5.1f} | "
          f"{min(r['temp'] for r in day):6.1f} {max(r['temp'] for r in day):6.1f} | {min(r['rh'] for r in day):6.0f} {max(r['rh'] for r in day):6.0f} {st.mean(daytime):6.0f} | "
          f"{heater_h:8.1f} | {mist_h:6.1f} | {sum(1 for r in day if r['vent'] > 10) * 0.5:11.1f}")
steps = [abs(b["temp"] - a["temp"]) for a, b in zip(rows, rows[1:])]
print(f"\ngreenhouse temperature: min {min(r['temp'] for r in rows):.1f}, max {max(r['temp'] for r in rows):.1f}, mean {st.mean(r['temp'] for r in rows):.1f} C")
print(f"largest change between samples (30 min): {max(steps):.2f} C, mean {st.mean(steps):.2f} C")
print(f"commands accepted {res['accepted']}, rejected by rules {res['rejected']}")
print("\nmean diurnal profile (hour: greenhouse C / outside C / RH %):")
for hr in range(0, 24, 2):
    sel = [r for r in rows if r["t"].hour == hr and r["t"].minute == 0]
    print(f"  {hr:02d}:00  {st.mean(r['temp'] for r in sel):5.1f} / {st.mean(r['out_temp'] for r in sel):5.1f} / {st.mean(r['rh'] for r in sel):4.0f}")
