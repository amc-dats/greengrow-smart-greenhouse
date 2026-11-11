"""Safety rules engine. Pure Python (no Django, no MQTT) so it can be tested exhaustively.

Every actuator command, whatever its source (automatic control, manual API call, approved assist
schedule), passes through check_command(). An empty list means the command is allowed; otherwise the
list holds the human-readable reasons it was refused. All times are SIMULATED time (UTC).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from greengrow_common import safety_limits as L

ACTIONS = {
    "pump": {"run", "stop"}, "vent": {"set"}, "doser": {"dose"}, "heater": {"run", "stop"},
    "humidifier": {"run", "stop"}, "lamp": {"run", "stop"}, "shade": {"set"},
}
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
    shade_pos: float | None = None
    light_today_mol: float = 0.0        # daily light integral so far today (mol/m2), from the sensor readings
    interval_s: float = L.DEFAULT_INTERVAL_S  # one sampling interval in simulated seconds
    sun: tuple[float, float] | None = None    # (sunrise, sunset) in decimal hours UTC today; None = use the fixed hours


# ------------------------------------------------------------ calendar helpers
def season(now: datetime) -> str:
    """'rest' in the winter months (deciduous Dendrobium rest, almost no water), else 'growing'."""
    return "rest" if now.month in L.REST_MONTHS else "growing"


def _hour(now: datetime) -> float:
    return now.hour + now.minute / 60 + now.second / 3600


def _at_hour(now: datetime, h: float) -> datetime:
    return now.replace(hour=int(h), minute=int((h % 1) * 60), second=0, microsecond=0)


def is_day(now: datetime, sun: tuple[float, float] | None = None) -> bool:
    """Day is sunrise to sunset when the sun times are known, else the fixed hours."""
    lo, hi = sun if sun else L.DAY_HOURS
    return lo <= _hour(now) < hi


def warm_up_hours(sun: tuple[float, float] | None = None) -> float:
    """Length of the gentle morning warm-up: a share of the day length (short winter days get a shorter warm-up), at most 5 h."""
    return L.HEATER_RAMP_HOURS if not sun else min(L.HEATER_RAMP_HOURS, L.WARM_UP_SHARE_OF_DAYLIGHT * (sun[1] - sun[0]))


def humidifier_window(sun: tuple[float, float] | None = None) -> tuple[float, float]:
    """Misting runs from sunrise until shortly before sunset, so leaves dry before night."""
    return (sun[0], sun[1] - L.HUMIDIFIER_END_BEFORE_SUNSET_H) if sun else L.HUMIDIFIER_HOURS


def humidifier_hours_ok(now: datetime, sun: tuple[float, float] | None = None) -> bool:
    lo, hi = humidifier_window(sun)
    return lo <= _hour(now) < hi


def lamp_window(sun: tuple[float, float] | None = None) -> tuple[float, float]:
    """The grow light's photoperiod: the day extended to PHOTOPERIOD_H hours in total, centred on solar noon (no extension when the
    day is already that long)."""
    if not sun:
        return L.LAMP_HOURS
    noon = (sun[0] + sun[1]) / 2
    return min(sun[0], noon - L.PHOTOPERIOD_H / 2), max(sun[1], noon + L.PHOTOPERIOD_H / 2)


def lamp_hours_ok(now: datetime, sun: tuple[float, float] | None = None) -> bool:
    lo, hi = lamp_window(sun)
    return lo <= _hour(now) < hi


def lamp_season(now: datetime) -> bool:
    """The grow light is a winter top-up only (the long darkness)."""
    return now.month in L.LAMP_MONTHS


def shade_season(now: datetime) -> bool:
    """The shade is for very strong summer light only."""
    return now.month in L.SHADE_MONTHS


def day_band(now: datetime) -> tuple[float, float]:
    """The desired daytime temperature range for the season."""
    return L.DAY_BAND_REST_C if season(now) == "rest" else L.DAY_BAND_C


def day_setpoint(now: datetime) -> float:
    return L.HEATER_DAY_SETPOINT_REST_C if season(now) == "rest" else L.HEATER_DAY_SETPOINT_C


def cold_danger(now: datetime) -> float:
    """The safety minimum temperature for the season (lower during the winter rest, by the grower's decision)."""
    return L.COLD_DANGER_C[season(now)]


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
        "lamp": {"run": {"duration_s"}, "stop": set()},
        "shade": {"set": {"position"}},
    }
    extra = set(params) - allowed[device][action]
    if extra:
        errs.append(f"unexpected parameters: {sorted(extra)}")
    limits = {("pump", "run"): L.PUMP_MAX_RUN_S, ("heater", "run"): L.HEATER_MAX_RUN_S,
              ("humidifier", "run"): L.HUMIDIFIER_MAX_RUN_S, ("lamp", "run"): L.LAMP_MAX_RUN_S}
    if (device, action) in limits:
        _whole(params, "duration_s", 1, limits[(device, action)], errs)
    elif (device, action) in (("vent", "set"), ("shade", "set")):
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


def _running(ctx: Context, device: str) -> bool:
    return any(
        h.device == device and h.action == "run" and h.sim_ts <= ctx.now < h.sim_ts + timedelta(seconds=h.params.get("duration_s", 0))
        for h in ctx.history
    )


def _last_pump(ctx: Context) -> datetime | None:
    runs = [h.sim_ts for h in ctx.history if h.device == "pump" and h.action == "run" and h.sim_ts <= ctx.now]
    return max(runs) if runs else None


def pump_gap_ok(ctx: Context) -> bool:
    last = _last_pump(ctx)
    return last is None or ctx.now - last >= timedelta(hours=L.PUMP_MIN_GAP_H[season(ctx.now)])


def _check_pump(p: dict, ctx: Context) -> list[str]:
    errs = []
    s = season(ctx.now)
    m = ctx.readings["soil_moisture"]
    target = L.MOISTURE_TARGET[s]
    if m >= target:
        errs.append(f"soil moisture {m:.1f}% is already at or above the {target:.0f}% target ({s} season)")
    projected = m + L.PUMP_NOMINAL_GAIN_PCT_PER_S * p["duration_s"]
    if projected > L.MOISTURE_CEILING:
        errs.append(f"projected moisture {projected:.1f}% would exceed the {L.MOISTURE_CEILING:.0f}% ceiling")
    if len(_recent(ctx, "pump", "run")) >= L.PUMP_MAX_RUNS_PER_DAY:
        errs.append(f"daily limit of {L.PUMP_MAX_RUNS_PER_DAY} irrigation runs reached")
    if not pump_gap_ok(ctx):
        errs.append(f"minimum gap of {L.PUMP_MIN_GAP_H[s]:g} h between irrigation runs not met ({s} season): let the medium dry down")
    return errs


def _check_humidifier(p: dict, ctx: Context) -> list[str]:
    errs = []
    r = ctx.readings
    if r["humidity"] >= L.HUMIDIFIER_MAX_RH:
        errs.append(f"humidity {r['humidity']:.0f}% is already at or above the {L.HUMIDIFIER_MAX_RH:g}% limit")
    if r["temperature"] < cold_danger(ctx.now):
        errs.append(f"too cold to mist (below the {cold_danger(ctx.now):g} C safety minimum)")
    if (ctx.vent_pos or 0) > L.HUMIDIFIER_VENT_MAX_OPEN_PCT:
        errs.append(f"vent is {ctx.vent_pos:.0f}% open: close it before misting")
    if not humidifier_hours_ok(ctx.now, ctx.sun):
        lo, hi = humidifier_window(ctx.sun)
        errs.append(f"misting is daytime only ({lo:05.2f}-{hi:05.2f} h UTC: sunrise to two hours before sunset)")
    return errs


def _check_heater(p: dict, ctx: Context) -> list[str]:
    errs = []
    temp = ctx.readings["temperature"]
    if temp >= L.HEATER_MAX_TEMP_C:
        errs.append(f"temperature {temp:.1f} C is already at or above the {L.HEATER_MAX_TEMP_C:g} C heating limit")
    if (ctx.vent_pos or 0) > L.HEATER_VENT_MAX_OPEN_PCT:
        errs.append(f"vent is {ctx.vent_pos:.0f}% open: close it before heating")
    return errs


def _check_lamp(p: dict, ctx: Context) -> list[str]:
    errs = []
    if not lamp_season(ctx.now):
        errs.append("the grow light is a winter top-up only (November to February)")
    if not lamp_hours_ok(ctx.now, ctx.sun):
        lo, hi = lamp_window(ctx.sun)
        errs.append(f"grow light only runs in its photoperiod window ({lo:05.2f}-{hi:05.2f} h UTC)")
    if ctx.readings["light"] >= L.LAMP_OFF_ABOVE_LUX:
        errs.append("it is already bright enough: no supplementary light")
    if ctx.light_today_mol >= L.DLI_BAND[1]:
        errs.append(f"daily light {ctx.light_today_mol:.1f} mol/m2 has reached the {L.DLI_BAND[1]:g} upper limit")
    return errs


def _check_vent(p: dict, ctx: Context) -> list[str]:
    errs = []
    if p["position"] > 0 and ctx.readings["temperature"] < cold_danger(ctx.now):
        errs.append(f"cold protection: vent may not open below the {cold_danger(ctx.now):g} C safety minimum")
    out = ctx.readings.get("outside_temperature")
    if out is not None and out < cold_danger(ctx.now) and p["position"] > L.VENT_COLD_OUTSIDE_MAX_POS:
        errs.append(f"outside air is {out:.1f} C, below the {cold_danger(ctx.now):g} C safety minimum: the vent may open at most {L.VENT_COLD_OUTSIDE_MAX_POS} %")
    if p["position"] > L.HEATER_VENT_MAX_OPEN_PCT and _running(ctx, "heater"):
        errs.append("the heater is running: the vent may not be opened")
    if p["position"] > L.HUMIDIFIER_VENT_MAX_OPEN_PCT and _running(ctx, "humidifier"):
        errs.append("the humidifier is running: the vent may not be opened")
    moves = [h for h in ctx.history if h.device == "vent" and h.sim_ts <= ctx.now]
    if moves and ctx.now - max(h.sim_ts for h in moves) < timedelta(minutes=L.VENT_MIN_DWELL_MIN):
        errs.append(f"minimum {L.VENT_MIN_DWELL_MIN:g} min between vent moves not met")
    return errs


def _check_shade(p: dict, ctx: Context) -> list[str]:
    moves = [h for h in ctx.history if h.device == "shade" and h.sim_ts <= ctx.now]
    if moves and ctx.now - max(h.sim_ts for h in moves) < timedelta(minutes=L.SHADE_MIN_DWELL_MIN):
        return [f"minimum {L.SHADE_MIN_DWELL_MIN:g} min between shade moves not met"]
    return []


def _check_doser(p: dict, ctx: Context) -> list[str]:
    errs = []
    if season(ctx.now) == "rest":
        errs.append("no nutrients during the winter rest")
    if not _running(ctx, "pump"):
        errs.append("nutrient dosing is only allowed while the irrigation pump is running")
    doses = _recent(ctx, "doser", "dose")
    if sum(h.params.get("volume_ml", 0) for h in doses) + p["volume_ml"] > L.DOSE_MAX_ML_PER_DAY:
        errs.append(f"daily nutrient limit of {L.DOSE_MAX_ML_PER_DAY} mL would be exceeded")
    if doses and ctx.now - max(h.sim_ts for h in doses) < timedelta(hours=L.DOSE_MIN_GAP_H):
        errs.append(f"minimum gap of {L.DOSE_MIN_GAP_H:g} h between nutrient doses not met")
    return errs


CHECKS = {"pump": _check_pump, "vent": _check_vent, "doser": _check_doser, "heater": _check_heater,
          "humidifier": _check_humidifier, "lamp": _check_lamp, "shade": _check_shade}


def check_command(cmd: dict, ctx: Context) -> list[str]:
    """Return the reasons a command is refused (empty list = allowed)."""
    errs = validate_structure(cmd)
    if errs:
        return errs
    device, action, params = cmd["device"], cmd["action"], cmd.get("params", {})
    # Moving towards the safe state is never blocked by data quality or timing rules.
    if action == "stop" or (device, action) in (("vent", "set"), ("shade", "set")) and params["position"] == 0:
        return []
    gate = _data_gate(ctx)
    if gate:
        return gate
    return CHECKS[device](params, ctx)


# ----------------------------------------------------------- automatic control
def _lamp_duty_last_interval(ctx: Context) -> float:
    """Fraction of the previous interval the grow light ran (its light is part of the last reading)."""
    for h in ctx.history:
        if h.device == "lamp" and h.action == "run" and abs((ctx.now - h.sim_ts).total_seconds() - ctx.interval_s) < 1:
            return min(1.0, h.params.get("duration_s", 0) / ctx.interval_s)
    return 0.0


def heater_target(now: datetime, sun: tuple[float, float] | None = None) -> tuple[float, float, float]:
    """The temperature setpoint, proportional band and warm-up ramp (0-1) in force now. The heater heats below the setpoint and the
    night vent cools above it (plus a margin), so the two share one target and never work against each other.
    Day (sunrise to sunset): the setpoint rises gradually after sunrise, then holds. Night: it eases down after sunset, then holds the night value."""
    night_sp = L.HEATER_NIGHT_SETPOINT_C[season(now)]
    day_sp = day_setpoint(now)
    lo, hi = sun if sun else L.DAY_HOURS
    h = _hour(now)
    if lo <= h < hi:
        ramp = min(1.0, max(0.0, (h - lo) / warm_up_hours(sun)))
        return (night_sp + (day_sp - night_sp) * ramp,
                L.HEATER_NIGHT_BAND_C + (L.HEATER_DAY_BAND_C - L.HEATER_NIGHT_BAND_C) * ramp, ramp)
    since_dusk = h - hi
    if 0 <= since_dusk < L.HEATER_EVENING_RAMP_HOURS:
        frac = since_dusk / L.HEATER_EVENING_RAMP_HOURS
        return (day_sp - (day_sp - night_sp) * frac,
                L.HEATER_DAY_BAND_C - (L.HEATER_DAY_BAND_C - L.HEATER_NIGHT_BAND_C) * frac, 1.0)
    return night_sp, L.HEATER_NIGHT_BAND_C, 1.0


def auto_decide(ctx: Context) -> list[dict]:
    """Rule-based controller: proposes commands from the latest readings. Proposals are NOT trusted;
    each one still has to pass check_command()."""
    r = ctx.readings
    if not r:
        return []
    out: list[dict] = []
    t, h, m, lux = r["temperature"], r["humidity"], r["soil_moisture"], r["light"]

    setpoint, band, ramp = heater_target(ctx.now, ctx.sun)

    # Vent. By day: hold the plateau (open above the top of the day band, close below 24 C, hold in between), but only if the
    # outside air is actually cooler. By night in the growing season: shed the stored daytime heat above the shared setpoint so the
    # night drops into the night band (the night drop is a flowering cue); the opening is SIZED from the outdoor reading when there is
    # one (and moves in small steps when there is not), and cools at most a couple of degrees per interval. No night venting in the
    # winter rest: nights cool into the band on their own and venting would only cost heat the next morning.
    out_t = r.get("outside_temperature")
    desired = None
    if is_day(ctx.now, ctx.sun):
        if t < L.VENT_CLOSE_TEMP_C:
            desired = 0
        elif t > L.VENT_OPEN_TEMP_C:
            if out_t is None or out_t < t - L.VENT_MIN_OUTSIDE_DELTA_C:
                desired = int(min(100, max(30, round((t - L.VENT_CLOSE_TEMP_C) / 8 * 10) * 10)))
            else:
                desired = 0  # the outside air is not cooler: opening would not help
        elif h > L.VENT_OPEN_HUMIDITY:
            desired = 50  # very humid but warm enough: ventilate for air movement
    elif season(ctx.now) == "growing":
        open_above = setpoint + L.NIGHT_VENT_OPEN_MARGIN_C
        floor_ok = t - cold_danger(ctx.now) >= L.NIGHT_VENT_MIN_ABOVE_FLOOR_C
        if out_t is not None:
            if t > open_above and out_t < t - L.NIGHT_VENT_MIN_OUTSIDE_DELTA_C and floor_ok:
                share = min(0.9, max(0.05, min(t - setpoint, L.NIGHT_VENT_MAX_DROP_C) / (t - out_t)))   # share of the gap to the outside to remove
                pos = -math.log(1 - share) / (L.VENT_NOMINAL_EXCHANGE_PER_MIN * ctx.interval_s / 60.0) * 100
                desired = int(min(L.NIGHT_VENT_SIZED_MAX_POS, max(10, round(pos / 10) * 10)))
            elif t <= setpoint + L.NIGHT_VENT_CLOSE_MARGIN_C or out_t >= t - 1.0:
                desired = 0
        elif t > open_above and floor_ok:
            desired = int(min(L.NIGHT_VENT_MAX_POS, max(10, round((t - setpoint) * 0.5) * 10)))
        elif t <= setpoint + L.NIGHT_VENT_CLOSE_MARGIN_C:
            desired = 0
    else:
        desired = 0  # winter rest: no night venting
    if desired and out_t is not None and out_t < cold_danger(ctx.now):
        desired = min(desired, L.VENT_COLD_OUTSIDE_MAX_POS)  # never propose what the cold-outside rule would refuse
    cur = ctx.vent_pos or 0
    if desired is not None and (abs(desired - cur) >= 10 or (desired == 0 and cur > 0)):
        out.append({"device": "vent", "action": "set", "params": {"position": desired}})
        cur = desired

    # Heater: PROPORTIONAL control to the shared setpoint (never while the vent is open). The run length per sampling interval
    # grows smoothly as the air falls below the setpoint; after dawn the heater is capped at a share of full power (a gentle warm-up).
    if t < setpoint and cur <= L.HEATER_VENT_MAX_OPEN_PCT:
        duty = min(1.0, (setpoint - t) / band)
        if is_day(ctx.now, ctx.sun) and ramp < 1.0:
            duty = min(duty, L.HEATER_RAMP_MAX_DUTY)
        duration = int(round(L.HEATER_MAX_RUN_S * duty))
        if duration >= 60:
            out.append({"device": "heater", "action": "run", "params": {"duration_s": duration}})

    # Humidifier: proportional (none at 65 %, full at 50 %). Daytime only, and only when the vent is closed.
    if h < L.HUMIDIFIER_TARGET_RH and humidifier_hours_ok(ctx.now, ctx.sun) and cur <= L.HUMIDIFIER_VENT_MAX_OPEN_PCT:
        duty = min(1.0, (L.HUMIDIFIER_TARGET_RH - h) / L.HUMIDIFIER_PROP_BAND_RH)
        duration = int(round(L.HUMIDIFIER_MAX_RUN_S * duty))
        if duration >= 60:
            out.append({"device": "humidifier", "action": "run", "params": {"duration_s": duration}})

    # Light: estimate the unshaded, lamp-free ambient light from the (interval-averaged) reading.
    shade = ctx.shade_pos or 0
    tx = 1 - (1 - L.SHADE_TRANSMISSION) * shade / 100
    ambient_lux = max(0.0, lux - _lamp_duty_last_interval(ctx) * L.LAMP_PPFD * L.LUX_PER_UMOL)
    ambient_open = ambient_lux / tx

    # Shade: cut glare, and stop the day's light overshooting the band. The decision looks ahead: today's light so far plus
    # what the current light rate is expected to add before the end of the day.
    day_end = _at_hour(ctx.now, ctx.sun[1] if ctx.sun else L.DAY_HOURS[1])
    rest_of_day_s = max(0.0, (day_end - ctx.now).total_seconds())
    projected = ctx.light_today_mol + L.LIGHT_PROJECTION_FACTOR * (ambient_open / L.LUX_PER_UMOL) * rest_of_day_s / 1e6
    want_closed = ambient_open >= L.SHADE_CLOSE_ABOVE_LUX or (projected > L.DLI_IDEAL[1] and ambient_open > 2000)
    want_open = ambient_open < L.SHADE_OPEN_BELOW_LUX and projected <= L.DLI_IDEAL[1]
    if not shade_season(ctx.now):
        want_closed, want_open = False, True   # outside the summer months the shade stays open
    elif not is_day(ctx.now, ctx.sun):
        want_closed, want_open = False, False  # at dusk, night and dawn the shade holds: it does not flip as the light fades
    shade_goal = 100 if want_closed else 0 if want_open else None
    if shade_goal is not None and abs(shade_goal - shade) >= 10:
        out.append({"device": "shade", "action": "set", "params": {"position": shade_goal}})

    # Grow light: top the day up to the daily light target, spreading the shortfall over the rest of the photoperiod.
    if lamp_season(ctx.now) and lamp_hours_ok(ctx.now, ctx.sun) and ctx.light_today_mol < L.DLI_TARGET and ambient_open < L.LAMP_OFF_ABOVE_LUX:
        end = _at_hour(ctx.now, lamp_window(ctx.sun)[1])
        remaining_s = (end - ctx.now).total_seconds()
        if remaining_s > 0:
            needed_ppfd = (L.DLI_TARGET - ctx.light_today_mol) * 1e6 / remaining_s
            duty = max(0.0, min(1.0, (needed_ppfd - tx * ambient_open / L.LUX_PER_UMOL) / L.LAMP_PPFD))
            duration = int(round(L.LAMP_MAX_RUN_S * duty))
            if duration >= 60:
                out.append({"device": "lamp", "action": "run", "params": {"duration_s": duration}})

    # Watering: a dry-down cycle. Trigger only when the medium has dried below the season's level AND the minimum
    # gap since the last watering has passed. In the winter rest the trigger is much lower.
    s = season(ctx.now)
    if m < L.MOISTURE_AUTO_START[s] and pump_gap_ok(ctx) and len(_recent(ctx, "pump", "run")) < L.PUMP_MAX_RUNS_PER_DAY:
        duration = min(L.PUMP_MAX_RUN_S, max(1, math.ceil((L.MOISTURE_TARGET[s] - m) / L.PUMP_NOMINAL_GAIN_PCT_PER_S)))
        out.append({"device": "pump", "action": "run", "params": {"duration_s": duration}})
    # Feeding is deliberately MANUAL: orchids are fed dilute fertiliser ("weakly, weekly") at the grower's discretion, never
    # more than the interlocked limits allow (see check_command). The controller never proposes a dose.
    return out


# ------------------------------------------------------------ assist schedules
def validate_schedule(items: list[dict], ctx: Context, allow_overdue: bool = False) -> list[str]:
    """Check a proposed watering schedule against the static limits.
    items: [{"due": aware datetime (sim time), "duration_s": int}]. Moisture-dependent rules are not
    predictable in advance and are enforced again, per run, when the item is executed.
    allow_overdue: at approval time an item may already be due (simulated time moves on while a person
    decides); it is then run at the next cycle, still subject to the per-run checks."""
    errs: list[str] = []
    past: list[datetime] = [h.sim_ts for h in ctx.history if h.device == "pump" and h.action == "run" and h.sim_ts <= ctx.now]
    for i, it in enumerate(items, 1):
        due, dur = it["due"], it["duration_s"]
        if isinstance(dur, bool) or not isinstance(dur, (int, float)) or dur != int(dur) or not 1 <= dur <= L.PUMP_MAX_RUN_S:
            errs.append(f"item {i}: duration_s must be a whole number between 1 and {L.PUMP_MAX_RUN_S}")
        if due > ctx.now + DAY or (due <= ctx.now and not allow_overdue):
            errs.append(f"item {i}: due time must fall within the next 24 simulated hours")
    pending = sorted(it["due"] for it in items)
    allt = sorted(past + pending)
    for t in pending:
        in_window = [x for x in allt if t - DAY < x <= t]
        if len(in_window) > L.PUMP_MAX_RUNS_PER_DAY:
            errs.append(f"more than {L.PUMP_MAX_RUNS_PER_DAY} irrigation runs within 24 h (at {t:%H:%M})")
            break
    for a, b in zip(allt, allt[1:]):
        gap_h = L.PUMP_MIN_GAP_H[season(b)]
        if b - a < timedelta(hours=gap_h):
            errs.append(f"runs at {a:%d %b %H:%M} and {b:%d %b %H:%M} are closer than the {gap_h:g} h minimum gap")
            break
    return errs
