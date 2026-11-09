"""Safety rules engine. Pure Python (no Django, no MQTT) so it can be tested exhaustively.

Every actuator command, whatever its source (automatic control, manual API call, approved assist
schedule), passes through check_command(). An empty list means the command is allowed; otherwise the
list holds the human-readable reasons it was refused. All times are SIMULATED time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from greengrow_common import safety_limits as L

ACTIONS = {"pump": {"run", "stop"}, "vent": {"set"}, "doser": {"dose"}, "heater": {"run", "stop"}, "humidifier": {"run", "stop"}}
DAY = timedelta(hours=24)


@dataclass
class HistoryItem:
    device: str
    action: str
    params: dict
    sim_ts: datetime


@dataclass
class Context:
    now: datetime
    readings: dict | None = None
    reading_age_s: float | None = None  # wall-clock age of the latest reading
    max_age_s: float = 3600.0
    history: list[HistoryItem] = field(default_factory=list)  # accepted commands only
    vent_pos: float | None = None


# ----------------------------------------------------------------- data checks
def plausibility(readings: dict) -> list[str]:
    out = []
    for key, (lo, hi) in L.PLAUSIBLE.items():
        v = readings.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            out.append(f"{key} missing or not a number")
        elif not lo <= v <= hi:
            out.append(f"{key}={v} outside plausible range [{lo}, {hi}]")
    return out


def _whole(params: dict, key: str, lo: int, hi: int, errs: list[str]) -> None:
    v = params.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
        errs.append(f"{key} must be a whole number")
    elif not lo <= v <= hi:
        errs.append(f"{key}={v} outside allowed range [{lo}, {hi}]")


def validate_structure(cmd: dict) -> list[str]:
    device, action, params = cmd.get("device"), cmd.get("action"), cmd.get("params", {})
    if device not in ACTIONS:
        return [f"unknown device '{device}'"]
    if action not in ACTIONS[device]:
        return [f"action '{action}' not supported by {device}"]
    if not isinstance(params, dict):
        return ["params must be an object"]
    errs: list[str] = []
    allowed = {
        "pump": {"run": {"duration_s"}, "stop": set()},
        "vent": {"set": {"position"}},
        "doser": {"dose": {"volume_ml"}},
        "heater": {"run": {"duration_s"}, "stop": set()},
        "humidifier": {"run": {"duration_s"}, "stop": set()},
    }
    extra = set(params) - allowed[device][action]
    if extra:
        errs.append(f"unexpected parameters: {sorted(extra)}")
    if (device, action) == ("pump", "run"):
        _whole(params, "duration_s", 1, L.PUMP_MAX_RUN_S, errs)
    elif (device, action) == ("heater", "run"):
        _whole(params, "duration_s", 1, L.HEATER_MAX_RUN_S, errs)
    elif (device, action) == ("humidifier", "run"):
        _whole(params, "duration_s", 1, L.HUMIDIFIER_MAX_RUN_S, errs)
    elif (device, action) == ("vent", "set"):
        _whole(params, "position", 0, 100, errs)
    elif (device, action) == ("doser", "dose"):
        _whole(params, "volume_ml", 1, L.DOSE_MAX_ML, errs)
    return errs


def _data_gate(ctx: Context) -> list[str]:
    if ctx.readings is None:
        return ["no sensor data yet: actuation inhibited"]
    if ctx.reading_age_s is None or ctx.reading_age_s > ctx.max_age_s:
        return [f"sensor data is stale (older than {ctx.max_age_s:.0f}s): actuation inhibited"]
    bad = plausibility(ctx.readings)
    if bad:
        return ["implausible sensor data, actuation inhibited: " + "; ".join(bad)]
    return []


# ---------------------------------------------------------------- per device
def _recent(ctx: Context, device: str, action: str) -> list[HistoryItem]:
    return [h for h in ctx.history if h.device == device and h.action == action and ctx.now - DAY < h.sim_ts <= ctx.now]


def _check_pump(p: dict, ctx: Context) -> list[str]:
    errs = []
    m = ctx.readings["soil_moisture"]
    if m >= L.MOISTURE_TARGET:
        errs.append(f"soil moisture {m:.1f}% is already at or above the {L.MOISTURE_TARGET:.0f}% target")
    projected = m + L.PUMP_NOMINAL_GAIN_PCT_PER_S * p["duration_s"]
    if projected > L.MOISTURE_CEILING:
        errs.append(f"projected moisture {projected:.1f}% would exceed the {L.MOISTURE_CEILING:.0f}% ceiling")
    runs = _recent(ctx, "pump", "run")
    if len(runs) >= L.PUMP_MAX_RUNS_PER_DAY:
        errs.append(f"daily limit of {L.PUMP_MAX_RUNS_PER_DAY} irrigation runs reached")
    if runs and ctx.now - max(h.sim_ts for h in runs) < timedelta(hours=L.PUMP_MIN_GAP_H):
        errs.append(f"minimum gap of {L.PUMP_MIN_GAP_H:g} h between irrigation runs not met")
    return errs


def _running(ctx: Context, device: str) -> bool:
    return any(
        h.device == device and h.action == "run" and h.sim_ts <= ctx.now < h.sim_ts + timedelta(seconds=h.params.get("duration_s", 0))
        for h in ctx.history
    )


def _heater_running(ctx: Context) -> bool:
    return _running(ctx, "heater")


def humidifier_hours_ok(now: datetime) -> bool:
    """Misting is daytime only, so leaves dry before night (wet leaves overnight invite rot and fungus)."""
    return L.HUMIDIFIER_HOURS[0] <= now.hour < L.HUMIDIFIER_HOURS[1]


def _check_humidifier(p: dict, ctx: Context) -> list[str]:
    errs = []
    r = ctx.readings
    if r["humidity"] >= L.HUMIDIFIER_MAX_RH:
        errs.append(f"humidity {r['humidity']:.0f}% is already at or above the {L.HUMIDIFIER_MAX_RH:g}% limit")
    if r["temperature"] < L.COLD_DANGER_C:
        errs.append(f"too cold to mist (below the {L.COLD_DANGER_C:g} C safety minimum)")
    if (ctx.vent_pos or 0) > L.HUMIDIFIER_VENT_MAX_OPEN_PCT:
        errs.append(f"vent is {ctx.vent_pos:.0f}% open: close it before misting")
    if not humidifier_hours_ok(ctx.now):
        errs.append(f"misting is daytime only ({L.HUMIDIFIER_HOURS[0]:02d}:00-{L.HUMIDIFIER_HOURS[1]:02d}:00)")
    return errs


def _check_heater(p: dict, ctx: Context) -> list[str]:
    errs = []
    temp = ctx.readings["temperature"]
    if temp >= L.HEATER_MAX_TEMP_C:
        errs.append(f"temperature {temp:.1f} C is already at or above the {L.HEATER_MAX_TEMP_C:g} C heating limit")
    if (ctx.vent_pos or 0) > L.HEATER_VENT_MAX_OPEN_PCT:
        errs.append(f"vent is {ctx.vent_pos:.0f}% open: close it before heating")
    return errs


def _check_vent(p: dict, ctx: Context) -> list[str]:
    errs = []
    if p["position"] > 0 and ctx.readings["temperature"] < L.COLD_DANGER_C:
        errs.append(f"cold protection: vent may not open below the {L.COLD_DANGER_C:g} C safety minimum")
    if p["position"] > L.HEATER_VENT_MAX_OPEN_PCT and _heater_running(ctx):
        errs.append("the heater is running: the vent may not be opened")
    if p["position"] > L.HUMIDIFIER_VENT_MAX_OPEN_PCT and _running(ctx, "humidifier"):
        errs.append("the humidifier is running: the vent may not be opened")
    moves = [h for h in ctx.history if h.device == "vent" and h.sim_ts <= ctx.now]
    if moves and ctx.now - max(h.sim_ts for h in moves) < timedelta(minutes=L.VENT_MIN_DWELL_MIN):
        errs.append(f"minimum {L.VENT_MIN_DWELL_MIN:g} min between vent moves not met")
    return errs


def _check_doser(p: dict, ctx: Context) -> list[str]:
    errs = []
    running = [
        h for h in ctx.history
        if h.device == "pump" and h.action == "run" and h.sim_ts <= ctx.now < h.sim_ts + timedelta(seconds=h.params.get("duration_s", 0))
    ]
    if not running:
        errs.append("nutrient dosing is only allowed while the irrigation pump is running")
    doses = _recent(ctx, "doser", "dose")
    if sum(h.params.get("volume_ml", 0) for h in doses) + p["volume_ml"] > L.DOSE_MAX_ML_PER_DAY:
        errs.append(f"daily nutrient limit of {L.DOSE_MAX_ML_PER_DAY} mL would be exceeded")
    if doses and ctx.now - max(h.sim_ts for h in doses) < timedelta(hours=L.DOSE_MIN_GAP_H):
        errs.append(f"minimum gap of {L.DOSE_MIN_GAP_H:g} h between nutrient doses not met")
    return errs


def check_command(cmd: dict, ctx: Context) -> list[str]:
    """Return the reasons a command is refused (empty list = allowed)."""
    errs = validate_structure(cmd)
    if errs:
        return errs
    device, action, params = cmd["device"], cmd["action"], cmd.get("params", {})
    # Moving towards the safe state is never blocked by data quality or timing rules.
    if action == "stop" or (device, action) == ("vent", "set") and params["position"] == 0:
        return []
    gate = _data_gate(ctx)
    if gate:
        return gate
    return {"pump": _check_pump, "vent": _check_vent, "doser": _check_doser, "heater": _check_heater, "humidifier": _check_humidifier}[device](params, ctx)


# ----------------------------------------------------------- automatic control
def auto_decide(ctx: Context) -> list[dict]:
    """Rule-based controller: proposes commands from the latest readings. Proposals are NOT trusted;
    each one still has to pass check_command()."""
    r = ctx.readings
    if not r:
        return []
    out: list[dict] = []
    t, h, m = r["temperature"], r["humidity"], r["soil_moisture"]

    # Vent: hold the daytime band (24-28 C). Open above the top of the band, close below the bottom, hold in between.
    desired = None
    if t < L.VENT_CLOSE_TEMP_C:
        desired = 0
    elif t > L.VENT_OPEN_TEMP_C:
        desired = int(min(100, max(30, round((t - L.VENT_CLOSE_TEMP_C) / 8 * 10) * 10)))
    elif h > L.VENT_OPEN_HUMIDITY:
        desired = 50  # very humid but warm enough: ventilate for air movement
    cur = ctx.vent_pos or 0
    if desired is not None and (abs(desired - cur) >= 10 or (desired == 0 and cur > 0)):
        out.append({"device": "vent", "action": "set", "params": {"position": desired}})
        cur = desired

    # Heater: protect the orchids from cold with PROPORTIONAL control. The run length per sampling interval grows
    # smoothly as the air falls below the setpoint (no heating at 20 C, full heating at 16 C), which avoids the
    # on/off cycling of a simple thermostat.
    if t < L.HEATER_TARGET_C:
        duty = min(1.0, (L.HEATER_TARGET_C - t) / L.HEATER_PROP_BAND_C)
        duration = int(round(L.HEATER_MAX_RUN_S * duty))
        if duration >= 60:
            out.append({"device": "heater", "action": "run", "params": {"duration_s": duration}})

    # Humidifier: PROPORTIONAL control of humidity for the orchids (none at 65 %, full at 50 %). Daytime only, and only
    # when the vent is closed, so no proposal is made (and none is refused and logged) when it could not run anyway.
    if h < L.HUMIDIFIER_TARGET_RH and humidifier_hours_ok(ctx.now) and cur <= L.HUMIDIFIER_VENT_MAX_OPEN_PCT:
        duty = min(1.0, (L.HUMIDIFIER_TARGET_RH - h) / L.HUMIDIFIER_PROP_BAND_RH)
        duration = int(round(L.HUMIDIFIER_MAX_RUN_S * duty))
        if duration >= 60:
            out.append({"device": "humidifier", "action": "run", "params": {"duration_s": duration}})

    if m < L.MOISTURE_AUTO_START:
        duration = min(L.PUMP_MAX_RUN_S, max(1, math.ceil((L.MOISTURE_TARGET - m) / L.PUMP_NOMINAL_GAIN_PCT_PER_S)))
        out.append({"device": "pump", "action": "run", "params": {"duration_s": duration}})
        out.append({"device": "doser", "action": "dose", "params": {"volume_ml": L.DOSE_DEFAULT_ML}})
    return out


# ------------------------------------------------------------ assist schedules
def validate_schedule(items: list[dict], ctx: Context, allow_overdue: bool = False) -> list[str]:
    """Check a proposed watering schedule against the static limits.
    items: [{"due": aware datetime (sim time), "duration_s": int}]. Moisture-dependent rules are not
    predictable in advance and are enforced again, per run, when the item is executed.
    allow_overdue: at approval time an item may already be due (simulated time moves on while a person
    decides); it is then run at the next cycle, still subject to the per-run checks."""
    errs: list[str] = []
    times: list[datetime] = [h.sim_ts for h in ctx.history if h.device == "pump" and h.action == "run" and ctx.now - DAY < h.sim_ts <= ctx.now]
    for i, it in enumerate(items, 1):
        due, dur = it["due"], it["duration_s"]
        if isinstance(dur, bool) or not isinstance(dur, (int, float)) or dur != int(dur) or not 1 <= dur <= L.PUMP_MAX_RUN_S:
            errs.append(f"item {i}: duration_s must be a whole number between 1 and {L.PUMP_MAX_RUN_S}")
        if due > ctx.now + DAY or (due <= ctx.now and not allow_overdue):
            errs.append(f"item {i}: due time must fall within the next 24 simulated hours")
    pending = sorted(it["due"] for it in items)
    allt = sorted(times + pending)
    for t in pending:
        in_window = [x for x in allt if t - DAY < x <= t]
        if len(in_window) > L.PUMP_MAX_RUNS_PER_DAY:
            errs.append(f"more than {L.PUMP_MAX_RUNS_PER_DAY} irrigation runs within 24 h (at {t:%H:%M})")
            break
    for a, b in zip(allt, allt[1:]):
        if b - a < timedelta(hours=L.PUMP_MIN_GAP_H):
            errs.append(f"runs at {a:%H:%M} and {b:%H:%M} are closer than the {L.PUMP_MIN_GAP_H:g} h minimum gap")
            break
    return errs
