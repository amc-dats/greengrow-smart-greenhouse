"""Day and night follow the sun: sunrise/sunset from date and location, and the rules that use them."""
from datetime import date, datetime, timezone

from greengrow_common import safety_limits as L
from greenhouse import rules, solar

LAT, LON = 52.06, -0.66   # Cranfield


def at(month, day, h, m=0):
    return datetime(2026, month, day, h, m, tzinfo=timezone.utc)


def test_cranfield_day_length_winter_versus_summer():
    wr, ws = solar.sun_times_utc(date(2025, 12, 29), LAT, LON)
    sr, ss = solar.sun_times_utc(date(2026, 6, 1), LAT, LON)
    assert 7.5 < ws - wr < 8.1          # about 7.8 h of daylight
    assert 16.0 < ss - sr < 16.8        # about 16.4 h
    assert 7.9 < wr < 8.5 and 15.7 < ws < 16.3


def test_polar_night_has_no_sun_times():
    assert solar.sun_times_utc(date(2026, 12, 21), 80.0, 0.0) is None


def test_is_day_follows_sun_not_fixed_hours():
    summer_sun = solar.sun_times_utc(date(2026, 6, 1), LAT, LON)
    assert rules.is_day(at(6, 1, 20, 0), summer_sun)          # 20:00 is still day in June
    assert not rules.is_day(at(6, 1, 20, 0))                  # but not under the fixed hours
    assert rules.is_day(at(6, 1, 5, 0), summer_sun)


def test_humidifier_window_ends_before_sunset():
    sun = solar.sun_times_utc(date(2025, 12, 29), LAT, LON)
    lo, hi = rules.humidifier_window(sun)
    assert abs(hi - (sun[1] - L.HUMIDIFIER_END_BEFORE_SUNSET_H)) < 1e-9 and lo == sun[0]


def test_lamp_tops_day_up_to_14_hour_photoperiod_centred_on_noon():
    sun = solar.sun_times_utc(date(2025, 12, 29), LAT, LON)
    lo, hi = rules.lamp_window(sun)
    assert abs((hi - lo) - L.PHOTOPERIOD_H) < 1e-9
    assert lo < sun[0] and hi > sun[1]
    long_day = solar.sun_times_utc(date(2026, 6, 1), LAT, LON)
    assert rules.lamp_window(long_day) == long_day               # day already longer than 14 h: no extension


def test_morning_warm_up_starts_at_sunrise_and_is_shorter_on_short_days():
    sun = solar.sun_times_utc(date(2025, 12, 29), LAT, LON)
    assert rules.warm_up_hours(sun) < L.HEATER_RAMP_HOURS
    sp0, _, ramp0 = rules.heater_target(at(12, 29, 8, 20), sun)
    sp1, _, ramp1 = rules.heater_target(at(12, 29, 13, 0), sun)
    assert ramp0 < 0.1 and ramp1 == 1.0 and sp1 > sp0


def test_winter_rest_has_cooler_day_band_and_setpoint():
    assert rules.day_band(at(1, 10, 12)) == L.DAY_BAND_REST_C
    assert rules.day_band(at(6, 10, 12)) == L.DAY_BAND_C
    assert rules.day_setpoint(at(1, 10, 12)) < rules.day_setpoint(at(6, 10, 12))
