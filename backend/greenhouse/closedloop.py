"""Closed-loop run of the simulator with the rules-based controller, without Django, MQTT or a database.

Actuators respond instantly and exactly to accepted commands. Used by the realism tests and by
scripts/closed_loop_report.py and scripts/orchid_report.py to show how the greenhouse behaves under a given
weather scenario.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from greengrow_common import safety_limits as L

from . import environment as env
from . import rules, solar


def run(weather, start: datetime, days: float = 14, step_min: float = 30) -> dict:
    state = env.initial_state(start, weather)
    interval_s = step_min * 60.0
    history: list[rules.HistoryItem] = []
    light_by_day: dict = {}                      # simulated date -> daily light integral so far (mol/m2)
    rows, accepted, rejected = [], 0, 0
    for _ in range(int(days * 24 * 60 / step_min)):
        state = env.advance(state, step_min, weather)
        vals = env.sample(state, weather, noise=False)
        vals["outside_temperature"] = env.outdoor_reading(state, weather, noise=False)
        day = state.sim_time.date()
        meta = getattr(weather, "meta", None) or {}
        sun = solar.sun_times_utc(day, meta.get("latitude") or L.SITE_LATITUDE, meta.get("longitude") if meta.get("longitude") is not None else L.SITE_LONGITUDE)
        light_by_day[day] = light_by_day.get(day, 0.0) + vals["light"] / L.LUX_PER_UMOL * interval_s / 1e6
        history = [h for h in history if h.sim_ts > state.sim_time - timedelta(days=10)]
        ctx = rules.Context(
            now=state.sim_time, readings=vals, reading_age_s=1.0, max_age_s=100.0, history=history,
            vent_pos=state.vent_pos, shade_pos=state.shade_pos, light_today_mol=light_by_day[day], interval_s=interval_s, sun=sun,
        )
        for cmd in rules.auto_decide(ctx):
            if rules.check_command(cmd, ctx):
                rejected += 1
                continue
            accepted += 1
            history.append(rules.HistoryItem(cmd["device"], cmd["action"], cmd["params"], state.sim_time))
            p = cmd["params"]
            if cmd["device"] == "pump":
                state = replace(state, pending_pump_s=state.pending_pump_s + p["duration_s"])
            elif cmd["device"] == "doser":
                state = replace(state, pending_dose_ml=state.pending_dose_ml + p["volume_ml"])
            elif cmd["device"] == "heater":
                state = replace(state, pending_heater_s=state.pending_heater_s + p["duration_s"])
            elif cmd["device"] == "humidifier":
                state = replace(state, pending_humidifier_s=state.pending_humidifier_s + p["duration_s"])
            elif cmd["device"] == "lamp":
                state = replace(state, pending_lamp_s=state.pending_lamp_s + p["duration_s"])
            elif cmd["device"] == "vent":
                state = replace(state, vent_pos=float(p["position"]))
                ctx.vent_pos = float(p["position"])
            elif cmd["device"] == "shade":
                state = replace(state, shade_pos=float(p["position"]))
                ctx.shade_pos = float(p["position"])
        out = env.outside(state, weather)
        rows.append({
            "t": state.sim_time, "temp": vals["temperature"], "rh": vals["humidity"], "moisture": vals["soil_moisture"],
            "light": vals["light"], "out_temp": out.temperature, "out_rh": out.humidity, "out_solar": out.solar,
            "vent": state.vent_pos, "shade": state.shade_pos, "heater_s": state.pending_heater_s,
            "mist_s": state.pending_humidifier_s, "lamp_s": state.pending_lamp_s, "pump_s": state.pending_pump_s, "sun": sun,
        })
    return {"rows": rows, "accepted": accepted, "rejected": rejected, "dli_by_day": light_by_day}
