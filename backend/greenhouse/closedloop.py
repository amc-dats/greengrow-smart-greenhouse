"""Closed-loop run of the simulator with the rules-based controller, without Django, MQTT or a database.

Actuators respond instantly and exactly to accepted commands. Used by the realism tests and by
scripts/closed_loop_report.py to show how the greenhouse behaves under a given weather scenario.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from . import environment as env
from . import rules


def run(weather, start: datetime, days: float = 14, step_min: float = 30) -> dict:
    state = env.initial_state(start, weather)
    history: list[rules.HistoryItem] = []
    rows, accepted, rejected = [], 0, 0
    for _ in range(int(days * 24 * 60 / step_min)):
        state = env.advance(state, step_min, weather)
        vals = env.sample(state, weather, noise=False)
        history = [h for h in history if h.sim_ts > state.sim_time - timedelta(hours=48)]
        ctx = rules.Context(now=state.sim_time, readings=vals, reading_age_s=1.0, max_age_s=100.0, history=history, vent_pos=state.vent_pos)
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
            elif cmd["device"] == "vent":
                state = replace(state, vent_pos=float(p["position"]))
                ctx.vent_pos = float(p["position"])
        out = env.outside(state, weather)
        rows.append({"t": state.sim_time, "temp": vals["temperature"], "rh": vals["humidity"], "moisture": vals["soil_moisture"],
                     "light": vals["light"], "out_temp": out.temperature, "out_rh": out.humidity, "vent": state.vent_pos,
                     "heater_s": state.pending_heater_s, "mist_s": state.pending_humidifier_s})
    return {"rows": rows, "accepted": accepted, "rejected": rejected}
