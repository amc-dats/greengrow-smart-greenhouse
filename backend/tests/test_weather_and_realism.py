"""Weather input and closed-loop realism, using the REAL cached Cranfield weather files."""
import statistics as st
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from greengrow_common import safety_limits as L
from greenhouse import closedloop
from greenhouse import weather as wx

DATA = Path(__file__).resolve().parent.parent / "greenhouse" / "data"


# ---------------------------------------------------------------- weather input
def test_weather_interpolates_between_hours_and_wraps_around():
    w = wx.WeatherData(datetime(2026, 1, 1, tzinfo=timezone.utc), [0.0, 10.0, 20.0], [50.0, 60.0, 70.0], [0.0, 100.0, 200.0])
    mid = w.at(datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc))
    assert (mid.temperature, mid.humidity, mid.solar) == (5.0, 55.0, 50.0)
    assert w.at(datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)).temperature == 0.0     # wrapped back to the start
    with pytest.raises(ValueError):
        wx.WeatherData(datetime(2026, 1, 1, tzinfo=timezone.utc), [1.0], [1.0], [1.0])


@pytest.mark.parametrize("name", ["winter", "summer"])
def test_cached_weather_files_are_complete_and_attributed(name):
    w = wx.WeatherData.load(DATA / f"cranfield_{name}.json")
    assert w.n == 14 * 24 and abs(w.meta["latitude"] - 52.06) < 0.1
    assert "Open-Meteo" in w.meta["attribution"]
    o = w.at(w.start + timedelta(hours=5, minutes=20))
    assert -20 < o.temperature < 40 and 0 <= o.humidity <= 100 and 0 <= o.solar < 1100


def test_winter_scenario_really_is_freezing():
    w = wx.WeatherData.load(DATA / "cranfield_winter.json")
    assert min(w.t) < 0 and sum(1 for x in w.t if x < 0) > 50


def test_missing_weather_file_falls_back_to_synthetic():
    assert isinstance(wx.load_weather("/nonexistent.json"), wx.SyntheticWeather)


# ------------------------------------------------- closed loop: is it realistic?
@pytest.fixture(scope="module")
def winter():
    w = wx.WeatherData.load(DATA / "cranfield_winter.json")
    return closedloop.run(w, w.start, days=14)["rows"]


@pytest.fixture(scope="module")
def summer():
    w = wx.WeatherData.load(DATA / "cranfield_summer.json")
    return closedloop.run(w, w.start, days=14)["rows"]


def test_orchids_never_get_near_freezing_in_the_coldest_fortnight(winter):
    assert min(r["temp"] for r in winter) >= L.COLD_DANGER_C                  # never below the 15 C safety minimum
    assert min(r["temp"] for r in winter) >= L.HEATER_TARGET_C - L.HEATER_PROP_BAND_C - 1   # and in practice well above it
    assert min(r["out_temp"] for r in winter) < 0                              # while it really was freezing outside


def test_temperature_is_smooth_not_swinging(winter, summer):
    for rows in (winter, summer):
        steps = [abs(b["temp"] - a["temp"]) for a, b in zip(rows, rows[1:])]
        assert max(steps) < 1.0 and st.mean(steps) < 0.3                       # no sensor-failure-like jumps
        assert max(r["temp"] for r in rows) - min(r["temp"] for r in rows) < 8.0


def test_daily_shape_slow_rise_afternoon_peak_gentle_decline(summer):
    by_hour = {h: st.mean(r["temp"] for r in summer if r["t"].hour == h and r["t"].minute == 0) for h in range(24)}
    peak_hour = max(by_hour, key=by_hour.get)
    assert 12 <= peak_hour <= 18                                               # hottest in the afternoon
    assert by_hour[4] < by_hour[peak_hour] - 1.5                               # cooler before dawn
    assert by_hour[peak_hour] - by_hour[22] < 4.0                              # declines gently after the peak
    assert max(r["temp"] for r in summer) <= L.DAY_BAND_C[1] + 1               # the vent/heater keep it in the orchid band


def test_greenhouse_is_warmer_than_outside_when_cold(winter):
    assert st.mean(r["temp"] - r["out_temp"] for r in winter) > 10


def test_humidifier_holds_daytime_humidity_in_the_orchid_band(winter, summer):
    for rows in (winter, summer):
        assert all(0 <= r["rh"] < L.HUMIDITY_BAND[1] for r in rows)                  # never above 80 %
        day = [r["rh"] for r in rows if 8 <= r["t"].hour < 17]
        assert st.mean(day) >= L.HUMIDITY_BAND[0] and min(day) > 40                  # daytime in (or close to) the 50-80 % band


def test_night_humidity_in_winter_is_still_low_known_limitation(winter):
    # Misting is daytime only (wet leaves overnight invite rot), so heated winter air is dry at night. This documents it.
    night = [r["rh"] for r in winter if r["t"].hour >= 21 or r["t"].hour < 6]
    assert st.mean(night) < L.HUMIDITY_BAND[0]
