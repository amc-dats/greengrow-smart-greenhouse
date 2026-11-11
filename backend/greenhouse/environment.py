"""Simulated greenhouse environment (the data generator's physics).

Pure Python, no Django. The OUTSIDE conditions (temperature, humidity, solar radiation) come from a weather
input (see weather.py: real hourly data for a location). The greenhouse reacts to them:

  * air temperature: exchanges heat with the outside air (slowly when the vent is closed, fast when open),
    gains heat from sunshine, is warmed by the heater, and is buffered by a thermal mass (structure, soil,
    water) that stores heat by day and releases it at night. This is what gives a slow morning rise, a held
    daytime level and a gentle evening decline instead of a curve that follows the sun.
  * humidity: tracked as water-vapour pressure, mixed with outside air, added to by the plants (transpiration);
    relative humidity is then derived from the air temperature, so it falls when the air warms.
  * soil moisture: dries with heat and light, rises when the irrigation pump runs.
  * light: outside solar radiation reduced by the greenhouse glazing.

Actuators act on the model: pump -> pending_pump_s, vent -> vent_pos, doser -> pending_dose_ml,
heater -> pending_heater_s (all reported back by the actuator node). The model is simple and NOT calibrated
to a particular building; the coefficients below are plausible round numbers chosen to produce realistic shapes.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from greengrow_common import safety_limits as L

from . import weather as wx

# --- greenhouse physics (rates in 1/minute) ---
K_LEAK = 1 / 300            # air exchange with outside, vent closed (5 h time constant: a well-insulated structure)
K_VENT = 1 / 20             # extra exchange when the vent is fully open (20 min)
SOLAR_RISE_C_PER_WM2 = 0.02  # closed-greenhouse steady rise per W/m2 of sunshine (700 W/m2 -> +14 C)
K_MASS_TO_AIR = 1 / 90      # thermal mass warms the air ...
K_AIR_TO_MASS = 1 / 360     # ... and is warmed by it (mass holds ~4x the heat of the air)
HEATER_GAIN_C_PER_MIN = 0.12  # heater output at full duty (can hold the greenhouse ~35 C above the outside air); an equipment-sizing assumption
LAMP_HEAT_C_PER_MIN = 0.015  # heat from the grow light at full power
HUMIDIFIER_GAIN_HPA_PER_MIN = 0.08  # water vapour added to the air by the fogger at full duty (fine mist: air only)
TRUE_PUMP_GAIN_PCT_PER_S = 0.14  # the controller assumes 0.15 (calibration error)
EVAP_FULL_RATE_ABOVE_PCT = 40.0   # at or above this moisture the medium dries at the full rate; below it the rate falls in proportion
EVAP_MIN_FACTOR = 0.1
GLAZING_TRANSMISSION = 0.7
LUX_PER_WM2 = 120.0
SUBSTEP_MIN = 5.0


@dataclass(frozen=True)
class SimState:
    sim_time: datetime
    soil_moisture: float = 55.0
    temperature: float = 20.0      # greenhouse air, C
    mass_temp: float = 20.0        # thermal mass, C
    vapour_hpa: float = 14.0       # water-vapour pressure of the greenhouse air
    vent_pos: float = 0.0
    pending_pump_s: float = 0.0
    pending_dose_ml: float = 0.0
    pending_heater_s: float = 0.0
    pending_humidifier_s: float = 0.0
    pending_lamp_s: float = 0.0
    shade_pos: float = 0.0
    light_lux: float = 0.0  # mean light over the last interval (what the light sensor reports)
    nutrient_ml_total: float = 0.0


def sat_vapour_hpa(temp_c: float) -> float:
    """Saturation vapour pressure (Magnus formula)."""
    return 6.112 * math.exp(17.62 * temp_c / (243.12 + temp_c))


def relative_humidity(state: SimState) -> float:
    return max(0.0, min(100.0, 100.0 * state.vapour_hpa / sat_vapour_hpa(state.temperature)))


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def initial_state(start: datetime, weather) -> SimState:
    """A pre-warmed greenhouse at the start of the run (so the first hours are not an artificial transient)."""
    o = weather.at(start)
    temp = 19.0  # a little above where the heater holds the greenhouse, so the first sample lands near it (no start-up transient)
    e_out = o.humidity / 100 * sat_vapour_hpa(o.temperature)
    return SimState(sim_time=start, temperature=temp, mass_temp=temp, vapour_hpa=min(e_out + 3.0, 0.7 * sat_vapour_hpa(temp)))


def outdoor_reading(state: SimState, weather, rng: random.Random | None = None, noise: bool = True) -> float:
    """What the outdoor temperature sensor reports (with a little sensor noise)."""
    r = rng or random
    return round(weather.at(state.sim_time).temperature + (r.gauss(0, 0.2) if noise else 0.0), 2)


def outside(state: SimState, weather) -> wx.Outside:
    return weather.at(state.sim_time)


def advance(state: SimState, minutes: float, weather) -> SimState:
    """Move the greenhouse forward by `minutes` of simulated time. Deterministic given the weather."""
    n = max(1, math.ceil(minutes / SUBSTEP_MIN))
    dt = minutes / n
    vent = state.vent_pos / 100
    duty = min(1.0, state.pending_heater_s / (minutes * 60))
    mist_duty = min(1.0, state.pending_humidifier_s / (minutes * 60))
    lamp_duty = min(1.0, state.pending_lamp_s / (minutes * 60))
    shade_factor = 1 - (1 - L.SHADE_TRANSMISSION) * state.shade_pos / 100   # shade cloth cuts the sunshine entering
    lux_sum = 0.0
    air, mass, vap, moist, t = state.temperature, state.mass_temp, state.vapour_hpa, state.soil_moisture, state.sim_time

    for _ in range(n):
        o = weather.at(t + timedelta(minutes=dt / 2))
        solar_in = o.solar * shade_factor
        lux_sum += solar_in * GLAZING_TRANSMISSION * LUX_PER_WM2 + lamp_duty * L.LAMP_PPFD * L.LUX_PER_UMOL
        k_ex = K_LEAK + K_VENT * vent
        d_air = (
            k_ex * (o.temperature - air)
            + K_LEAK * SOLAR_RISE_C_PER_WM2 * solar_in
            + LAMP_HEAT_C_PER_MIN * lamp_duty
            + HEATER_GAIN_C_PER_MIN * duty
            + K_MASS_TO_AIR * (mass - air)
        )
        d_mass = K_AIR_TO_MASS * (air - mass)
        air += d_air * dt
        mass += d_mass * dt

        e_out = o.humidity / 100 * sat_vapour_hpa(o.temperature)
        transpiration = (0.004 + 0.03 * _clamp(solar_in / 700, 0, 1)) * (moist / 60)
        vap += (k_ex * (e_out - vap) + transpiration + HUMIDIFIER_GAIN_HPA_PER_MIN * mist_duty) * dt
        vap = _clamp(vap, 0.5, sat_vapour_hpa(air))  # above saturation the excess condenses

        # a drier medium gives up water more slowly (the film left on the bark is held harder), so it tails off instead of hitting 0
        evap_pct_per_min = 0.03 * (1 + 0.04 * (air - 15)) * (0.4 + _clamp(solar_in / 800, 0, 1)) * _clamp(moist / EVAP_FULL_RATE_ABOVE_PCT, EVAP_MIN_FACTOR, 1.0)
        moist -= max(0.0, evap_pct_per_min) * dt
        t += timedelta(minutes=dt)

    moist = _clamp(moist + TRUE_PUMP_GAIN_PCT_PER_S * state.pending_pump_s, 0.0, 100.0)
    return replace(
        state,
        sim_time=state.sim_time + timedelta(minutes=minutes),
        soil_moisture=moist, temperature=air, mass_temp=mass, vapour_hpa=vap,
        pending_pump_s=0.0, pending_dose_ml=0.0, pending_heater_s=0.0, pending_humidifier_s=0.0, pending_lamp_s=0.0,
        light_lux=lux_sum / n,
        nutrient_ml_total=state.nutrient_ml_total + state.pending_dose_ml,
    )


def sample(state: SimState, weather, rng: random.Random | None = None, noise: bool = True, fault: dict | None = None) -> dict:
    """What the four sensors report at state.sim_time (with sensor noise and an optional injected fault)."""
    r = rng or random
    vals = {
        "soil_moisture": state.soil_moisture,
        "temperature": state.temperature,
        "humidity": relative_humidity(state),
        "light": state.light_lux,
    }
    if noise:
        vals["soil_moisture"] = _clamp(vals["soil_moisture"] + r.gauss(0, 0.3), 0.0, 100.0)
        vals["temperature"] += r.gauss(0, 0.1)
        vals["humidity"] = _clamp(vals["humidity"] + r.gauss(0, 0.5), 0.0, 100.0)
        vals["light"] = max(0.0, vals["light"] + (r.gauss(0, 150) if vals["light"] > 0 else 0.0))
    if fault and fault.get("metric") in vals:
        vals[fault["metric"]] = float(fault["value"])  # deliberately NOT clamped: tests the safety layer
    return {k: round(v, 2) for k, v in vals.items()}
