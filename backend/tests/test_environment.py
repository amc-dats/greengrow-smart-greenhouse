from dataclasses import replace
from datetime import datetime, timezone

from greengrow_common import safety_limits as L
from greenhouse import environment as env
from greenhouse import weather as wx

W = wx.SyntheticWeather()
START = datetime(2026, 1, 5, 6, 0, tzinfo=timezone.utc)


def state(**kw):
    return replace(env.initial_state(START, W), **kw)


def run(s, hours, step_min=30):
    for _ in range(int(hours * 60 / step_min)):
        s = env.advance(s, step_min, W)
    return s


def test_model_is_deterministic():
    assert run(state(), 12) == run(state(), 12)


def test_soil_dries_without_irrigation_and_rises_with_it():
    assert run(state(soil_moisture=55.0), 24).soil_moisture < 55.0
    watered = env.advance(state(pending_pump_s=100.0), 30, W)
    dry = env.advance(state(), 30, W)
    assert watered.soil_moisture - dry.soil_moisture > 10
    assert watered.pending_pump_s == 0.0


def test_open_vent_cools_a_sunny_greenhouse():
    noon = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    base = replace(env.initial_state(noon, W), temperature=22.0, mass_temp=22.0)
    closed, opened = base, replace(base, vent_pos=100.0)
    for _ in range(8):
        closed, opened = env.advance(closed, 30, W), env.advance(opened, 30, W)
    assert opened.temperature < closed.temperature - 3


def test_heater_warms_the_greenhouse_and_lowers_relative_humidity():
    cold = state(temperature=15.0, mass_temp=15.0)
    off = env.advance(cold, 30, W)
    on = env.advance(replace(cold, pending_heater_s=1800.0), 30, W)
    assert on.temperature > off.temperature + 1.5
    assert env.relative_humidity(on) < env.relative_humidity(off)       # warmer air, same moisture: lower RH
    assert on.pending_heater_s == 0.0


def test_humidifier_raises_humidity_without_changing_temperature_or_soil():
    base = state()
    off = env.advance(base, 30, W)
    on = env.advance(replace(base, pending_humidifier_s=1800.0), 30, W)
    assert env.relative_humidity(on) > env.relative_humidity(off) + 5
    assert abs(on.temperature - off.temperature) < 1e-9 and abs(on.soil_moisture - off.soil_moisture) < 1e-9   # fine mist: air only
    assert on.pending_humidifier_s == 0.0


def test_thermal_mass_delays_cooling_overnight():
    warm_mass = state(temperature=24.0, mass_temp=24.0)
    cold_mass = state(temperature=24.0, mass_temp=10.0)
    assert run(warm_mass, 3).temperature > run(cold_mass, 3).temperature


def test_temperature_changes_gradually():
    s, prev = state(), state().temperature
    for _ in range(96):
        s = env.advance(s, 30, W)
        assert abs(s.temperature - prev) < 2.0
        prev = s.temperature


def test_humidity_stays_physical_and_condensation_is_capped():
    s = state(vapour_hpa=50.0)                     # far above saturation
    s = env.advance(s, 30, W)
    assert s.vapour_hpa <= env.sat_vapour_hpa(s.temperature) + 1e-9
    assert 0 <= env.relative_humidity(s) <= 100


def test_light_is_the_interval_mean_through_the_glazing_shade_and_lamp():
    noon = replace(env.initial_state(datetime(2026, 1, 5, 11, 45, tzinfo=timezone.utc), W), temperature=22.0, mass_temp=22.0)
    open_ = env.advance(noon, 30, W)
    assert 45000 < open_.light_lux < 51000                         # about 600 W/m2 x 0.7 glazing x 120 lux per W/m2
    shaded = env.advance(replace(noon, shade_pos=100.0), 30, W)
    assert abs(shaded.light_lux - open_.light_lux * L.SHADE_TRANSMISSION) < 1.0            # a 50 % shade cloth halves it
    lit = env.advance(replace(noon, pending_lamp_s=1800.0), 30, W)
    assert abs((lit.light_lux - open_.light_lux) - L.LAMP_PPFD * L.LUX_PER_UMOL) < 1.0     # the lamp adds a fixed amount
    assert lit.temperature > open_.temperature and shaded.temperature < open_.temperature  # lamp warms, shade cools
    night = env.advance(env.initial_state(datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc), W), 30, W)
    assert night.light_lux == 0.0
    assert env.sample(open_, W, noise=False)["light"] == round(open_.light_lux, 2)         # the sensor reports that mean


def test_sensor_noise_stays_physical_and_fault_overrides():
    import random

    rng = random.Random(1)
    s = state(soil_moisture=99.9)
    for _ in range(200):
        v = env.sample(s, W, rng=rng)
        assert 0 <= v["soil_moisture"] <= 100 and 0 <= v["humidity"] <= 100 and v["light"] >= 0
    assert env.sample(s, W, noise=False, fault={"metric": "temperature", "value": 99})["temperature"] == 99.0


def test_nutrient_total_accumulates():
    s = env.advance(state(pending_dose_ml=20.0), 30, W)
    assert s.nutrient_ml_total == 20.0 and s.pending_dose_ml == 0.0
