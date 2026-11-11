"""Orchid 'thriving' report: runs the simulator and the rules-based controller on a weather scenario and scores it
against the measurable targets in docs/ORCHID_TARGETS.md (Cattleya and Dendrobium).

  docker run --rm -i -v "$PWD:/w:ro" -e PYTHONPATH=/w/backend:/w/common python:3.12-slim \
      python - winter < scripts/orchid_report.py        # or: summer
"""
import statistics as st
import sys
from datetime import timedelta

from greengrow_common import safety_limits as L
from greenhouse import closedloop, orchid_targets as ot, weather as wx

scenario = sys.argv[1] if len(sys.argv) > 1 else "winter"
w = wx.WeatherData.load(f"/w/backend/greenhouse/data/cranfield_{scenario}.json")
res = closedloop.run(w, w.start, days=14)
score = ot.score(res, w.start.date(), 14)

print(f"scenario: {scenario} ({w.meta.get('latitude'):.2f}, {w.meta.get('longitude'):.2f}), starts {w.start:%Y-%m-%d}")
print(f"{'target':<88} {'result':>7} {'needed':>7}  verdict")
for t in score["targets"]:
    print(f"{t['name']:<88} {t['value']:6.0f}% {t['needed']:6.0f}%  {'PASS' if t['ok'] else 'FAIL'}")
s = score["summary"]
print(f"\ntemperature: day {s['day_mean']:.1f} C, night low (mean) {s['night_low_mean']:.1f} C, overall min {s['min']:.1f} C, max {s['max']:.1f} C, "
      f"day-night difference {s['diff_mean']:.1f} C")
print(f"daily light (mol/m2/day): mean {s['dli_mean']:.1f}, min {s['dli_min']:.1f}, max {s['dli_max']:.1f}; peak {s['peak_klux']:.1f} klux")
print(f"actuator hours per day: heater {s['heater_h']:.1f}, lamp {s['lamp_h']:.1f}, humidifier {s['mist_h']:.1f}, shade closed {s['shade_h']:.1f}; waterings {s['waterings']}")
print(f"commands accepted {res['accepted']}, rejected by rules {res['rejected']}")
raise SystemExit(0 if all(t["ok"] for t in score["targets"]) else 1)
