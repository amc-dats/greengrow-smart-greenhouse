"""Scores a closed-loop run against the measurable orchid targets (docs/ORCHID_TARGETS.md).

These are the testable form of the goal "keep the orchids healthy and thriving": each target is a pass/fail check with a
threshold. Used by the tests and by scripts/orchid_report.py. Pure Python, no Django.
"""
from __future__ import annotations

import statistics as st
from datetime import timedelta

from greengrow_common import safety_limits as L

from . import rules

SKIP_FIRST_ROWS = 2   # the first hour is start-up


def _pct(flags) -> float:
    flags = list(flags)
    return 100.0 * sum(flags) / len(flags) if flags else float("nan")


def score(res: dict, first_day, days: int = 14) -> dict:
    rows = res["rows"][SKIP_FIRST_ROWS:]
    dli = {d: v for d, v in res["dli_by_day"].items() if first_day <= d < first_day + timedelta(days=days)}

    def hrs(r):
        return r["t"].hour + r["t"].minute / 60

    def sun(r):
        return r.get("sun") or L.DAY_HOURS

    def in_day_plateau(r):   # after the gentle warm-up (plus an hour) until sunset
        lo, hi = sun(r)
        return lo + rules.warm_up_hours(r.get("sun")) + 1 <= hrs(r) < hi

    def in_deep_night(r):    # from three hours after sunset to an hour before sunrise
        lo, hi = sun(r)
        return hrs(r) >= hi + 3 or hrs(r) < lo - 1

    # a "night" runs from sunset to the next sunrise; its low is the coldest sample after the evening ease-down.
    night_lows: dict = {}
    for r in rows:
        lo, hi = sun(r)
        if hrs(r) >= hi + 3 or hrs(r) < lo:
            key = (r["t"] - timedelta(hours=lo + 1)).date()
            night_lows[key] = min(night_lows.get(key, 99.0), r["temp"])
    # drop the partial night at the very start of the run
    night_lows = {k: v for k, v in night_lows.items() if k >= first_day}

    day_rows = [r for r in rows if in_day_plateau(r)]
    in_band = lambda r: rules.day_band(r['t'])[0] <= r['temp'] <= rules.day_band(r['t'])[1]
    deep_night = [r for r in rows if in_deep_night(r)]
    hum_day = [r for r in rows if rules.humidifier_window(r.get("sun"))[0] + 1 <= hrs(r) < rules.humidifier_window(r.get("sun"))[1]]
    diffs = []
    for d in sorted({r["t"].date() for r in rows}):
        dr = [r["temp"] for r in day_rows if r["t"].date() == d]
        nr = [r["temp"] for r in deep_night if r["t"].date() == d]
        if dr and nr:
            diffs.append(st.mean(dr) - st.mean(nr))

    floor_ok = all(r["temp"] >= rules.cold_danger(r["t"]) for r in rows)
    night_ok = []
    for k, low in night_lows.items():
        lo, hi = L.NIGHT_BAND_C[rules.season(rows[0]["t"].replace(year=k.year, month=k.month, day=k.day))]
        night_ok.append(lo <= low <= hi)

    targets = [
        ("T1 never below the season's safety minimum (15 C; 10 C in the winter rest)", 100.0 if floor_ok else 0.0, 100.0),
        ("T2 each night's low inside the season's night band (15-18 C; 10-14 C in the rest)", _pct(night_ok), 80.0),
        (f"T3 daytime (after the warm-up, until sunset) inside the season's day band ({L.DAY_BAND_C[0]:g}-{L.DAY_BAND_C[1]:g} C; {L.DAY_BAND_REST_C[0]:g}-{L.DAY_BAND_REST_C[1]:g} C in the rest)", _pct(in_band(r) for r in day_rows), 80.0),
        (f"T4 day-night difference of at least {L.DAY_NIGHT_DIFF_MIN_C:g} C (days meeting it)", _pct(x >= L.DAY_NIGHT_DIFF_MIN_C for x in diffs), 80.0),
        (f"T5 daytime humidity inside {L.HUMIDITY_BAND[0]:g}-{L.HUMIDITY_BAND[1]:g} %", _pct(L.HUMIDITY_BAND[0] <= r["rh"] <= L.HUMIDITY_BAND[1] for r in hum_day), 90.0),
        (f"T6 daily light {L.DLI_BAND[0]:g}-{L.DLI_BAND[1]:g} mol/m2/day, acceptable for both genera (days meeting it)", _pct(L.DLI_BAND[0] <= v <= L.DLI_BAND[1] for v in dli.values()), 80.0),
        (f"T7 light at most {L.PEAK_LUX_MAX / 1000:.1f} klux (samples meeting it)", _pct(r["light"] <= L.PEAK_LUX_MAX for r in rows), 95.0),
    ]
    out = [{"name": n, "value": v, "needed": need, "ok": v >= need} for n, v, need in targets]
    summary = {
        "day_mean": st.mean(r["temp"] for r in day_rows), "night_low_mean": st.mean(night_lows.values()) if night_lows else float("nan"),
        "min": min(r["temp"] for r in rows), "max": max(r["temp"] for r in rows), "diff_mean": st.mean(diffs) if diffs else float("nan"),
        "dli_mean": st.mean(dli.values()), "dli_min": min(dli.values()), "dli_max": max(dli.values()),
        "peak_klux": max(r["light"] for r in rows) / 1000,
        "heater_h": sum(1 for r in rows if r["heater_s"]) * 0.5 / days, "lamp_h": sum(1 for r in rows if r["lamp_s"]) * 0.5 / days,
        "mist_h": sum(1 for r in rows if r["mist_s"]) * 0.5 / days, "shade_h": sum(1 for r in rows if r["shade"] > 50) * 0.5 / days,
        "waterings": sum(1 for r in rows if r["pump_s"]),
    }
    return {"targets": out, "summary": summary}
