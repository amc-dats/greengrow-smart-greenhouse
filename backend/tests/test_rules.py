"""Unit tests of the safety rules engine: one group per device, plus the seasons, the automatic controller and schedules.
Boundary values are tested on both sides of each limit (a 'boundary value analysis')."""
from datetime import datetime, timedelta, timezone

from greengrow_common import safety_limits as L
from greenhouse import rules

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)        # growing season, day, shade month, not a lamp month
JAN = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)       # winter rest, day, lamp month
H = timedelta(hours=1)


def ctx(**kw):
    base = dict(
        now=NOW,
        readings={"soil_moisture": 25.0, "temperature": 22.0, "humidity": 60.0, "light": 10000.0},
        reading_age_s=5.0, max_age_s=3600.0, history=[], vent_pos=0.0, shade_pos=0.0,
    )
    base.update(kw)
    return rules.Context(**base)


def reading(**kw):
    r = {"soil_moisture": 25.0, "temperature": 22.0, "humidity": 60.0, "light": 10000.0}
    r.update(kw)
    return r


def at_hour(h, month_ctx=NOW, **kw):
    return ctx(now=month_ctx.replace(hour=h), **kw)


def pump(d=60):
    return {"device": "pump", "action": "run", "params": {"duration_s": d}}


def vent(p):
    return {"device": "vent", "action": "set", "params": {"position": p}}


def dose(v=20):
    return {"device": "doser", "action": "dose", "params": {"volume_ml": v}}


def heater(d=900):
    return {"device": "heater", "action": "run", "params": {"duration_s": d}}


def mist(d=900):
    return {"device": "humidifier", "action": "run", "params": {"duration_s": d}}


def lamp(d=900):
    return {"device": "lamp", "action": "run", "params": {"duration_s": d}}


def shade(p):
    return {"device": "shade", "action": "set", "params": {"position": p}}


def hist(device, action, ago, now=NOW, **params):
    return rules.HistoryItem(device, action, params, now - ago)


def auto(device, c):
    return [x for x in rules.auto_decide(c) if x["device"] == device]


# ------------------------------------------------------------------ structure
def test_valid_pump_run_is_allowed():
    assert rules.check_command(pump(60), ctx()) == []


def test_unknown_device_and_action():
    assert rules.check_command({"device": "toaster", "action": "run", "params": {}}, ctx())
    assert rules.check_command({"device": "pump", "action": "dose", "params": {}}, ctx())


def test_parameter_bounds_and_types():
    assert rules.check_command(pump(121), ctx())      # above hard maximum
    assert rules.check_command(pump(0), ctx())
    assert rules.check_command(pump(60.5), ctx())     # not a whole number
    assert rules.check_command(pump(True), ctx())     # bool is not a number here
    assert rules.check_command(vent(101), ctx())
    assert rules.check_command(shade(101), ctx())
    assert rules.check_command(dose(51), ctx())
    assert rules.check_command(lamp(1801), ctx(now=JAN))
    assert rules.check_command({"device": "pump", "action": "run", "params": {"duration_s": 30, "speed": 9}}, ctx())


# ---------------------------------------------------------------- data gates
def test_no_data_stale_and_implausible_block_actuation_but_not_stop():
    assert rules.check_command(pump(), ctx(readings=None))
    assert rules.check_command(pump(), ctx(reading_age_s=4000.0))
    assert rules.check_command(pump(), ctx(reading_age_s=None))
    assert rules.check_command(pump(), ctx(readings=reading(temperature=999.0)))
    assert rules.check_command(pump(), ctx(readings=reading(soil_moisture=float("nan"))))
    for dev in ("pump", "heater", "humidifier", "lamp"):
        assert rules.check_command({"device": dev, "action": "stop", "params": {}}, ctx(readings=None)) == []   # safe direction
    assert rules.check_command(vent(0), ctx(reading_age_s=9999.0)) == []
    assert rules.check_command(shade(0), ctx(reading_age_s=9999.0)) == []


# --------------------------------------------------------------------- seasons
def test_season_and_day_helpers():
    assert rules.season(JAN) == "rest" and rules.season(NOW) == "growing"
    assert rules.season(datetime(2026, 12, 1, tzinfo=timezone.utc)) == "rest" and rules.season(datetime(2026, 3, 1, tzinfo=timezone.utc)) == "growing"
    assert rules.is_day(NOW.replace(hour=7)) and not rules.is_day(NOW.replace(hour=19)) and not rules.is_day(NOW.replace(hour=6))
    assert rules.lamp_season(JAN) and not rules.lamp_season(NOW)
    assert rules.shade_season(NOW) and not rules.shade_season(JAN)
    assert rules.cold_danger(NOW) == 15.0 and rules.cold_danger(JAN) == 10.0


# ---------------------------------------------------------------------- pump
def test_pump_refused_when_moisture_at_target_or_ceiling_would_be_exceeded():
    assert rules.check_command(pump(30), ctx(readings=reading(soil_moisture=60.0)))          # at the growing-season target
    assert rules.check_command(pump(30), ctx(readings=reading(soil_moisture=59.9))) == []    # just under it
    assert rules.check_command(pump(120), ctx(readings=reading(soil_moisture=55.0)))         # 55 + 18 > 70
    assert rules.check_command(pump(30), ctx(readings=reading(soil_moisture=55.0))) == []


def test_pump_dry_down_gap_daily_limit():
    two = [hist("pump", "run", 23 * H, duration_s=60), hist("pump", "run", 20 * H, duration_s=60)]
    assert any("daily limit" in r for r in rules.check_command(pump(), ctx(history=two)))
    assert any("minimum gap" in r for r in rules.check_command(pump(), ctx(history=[hist("pump", "run", 47 * H, duration_s=60)])))   # 48 h gap
    assert rules.check_command(pump(), ctx(history=[hist("pump", "run", 49 * H, duration_s=60)])) == []


def test_winter_rest_waters_much_less():
    j = ctx(now=JAN, readings=reading(soil_moisture=35.0))
    assert rules.check_command(pump(30), j) == []                                 # below the rest target of 40 %
    assert any("target" in r for r in rules.check_command(pump(30), ctx(now=JAN, readings=reading(soil_moisture=40.0))))
    recent = [hist("pump", "run", 5 * 24 * H, now=JAN, duration_s=60)]
    assert any("168 h" in r for r in rules.check_command(pump(30), ctx(now=JAN, readings=reading(soil_moisture=35.0), history=recent)))
    old = [hist("pump", "run", 8 * 24 * H, now=JAN, duration_s=60)]
    assert rules.check_command(pump(30), ctx(now=JAN, readings=reading(soil_moisture=35.0), history=old)) == []


# ---------------------------------------------------------------------- vent
def test_vent_cold_protection_blocks_opening_not_closing():
    cold = ctx(readings=reading(temperature=14.0))            # below the 15 C growing-season minimum
    assert any("cold protection" in r for r in rules.check_command(vent(50), cold))
    assert rules.check_command(vent(0), cold) == []
    assert rules.check_command(vent(50), ctx(readings=reading(temperature=15.0))) == []


def test_winter_rest_allows_a_lower_safety_minimum():
    assert rules.check_command(vent(50), ctx(now=JAN, readings=reading(temperature=11.0))) == []                  # 10 C floor
    assert rules.check_command(vent(50), ctx(now=JAN, readings=reading(temperature=9.9)))


def test_vent_minimum_dwell():
    recent = [rules.HistoryItem("vent", "set", {"position": 100}, NOW - timedelta(minutes=5))]
    assert rules.check_command(vent(50), ctx(history=recent))
    old = [rules.HistoryItem("vent", "set", {"position": 100}, NOW - timedelta(minutes=30))]
    assert rules.check_command(vent(50), ctx(history=old)) == []


# --------------------------------------------------------------------- doser
def test_doser_only_while_pump_running():
    assert any("only allowed while" in r for r in rules.check_command(dose(), ctx()))
    running = [rules.HistoryItem("pump", "run", {"duration_s": 60}, NOW)]
    assert rules.check_command(dose(), ctx(history=running)) == []
    finished = [rules.HistoryItem("pump", "run", {"duration_s": 60}, NOW - timedelta(minutes=30))]
    assert rules.check_command(dose(), ctx(history=finished))


def test_doser_daily_limit_gap_and_no_fertiliser_in_the_rest():
    running = rules.HistoryItem("pump", "run", {"duration_s": 60}, NOW)
    four = [hist("doser", "dose", ago, volume_ml=50) for ago in (13 * H, 17 * H, 20 * H, 23 * H)]
    assert any("daily nutrient" in r for r in rules.check_command(dose(20), ctx(history=[running] + four)))
    recent = [running, hist("doser", "dose", 2 * H, volume_ml=20)]
    assert any("minimum gap" in r for r in rules.check_command(dose(20), ctx(history=recent)))
    j_running = [rules.HistoryItem("pump", "run", {"duration_s": 60}, JAN)]
    assert any("winter rest" in r for r in rules.check_command(dose(), ctx(now=JAN, history=j_running)))


# -------------------------------------------------------------------- heater
def test_heater_runs_when_cool_and_is_bounded():
    assert rules.check_command(heater(900), ctx(readings=reading(temperature=17.0))) == []
    assert rules.check_command(heater(1801), ctx(readings=reading(temperature=17.0)))       # over the hard maximum
    assert any("heating limit" in r for r in rules.check_command(heater(), ctx(readings=reading(temperature=27.0))))
    assert rules.check_command(heater(), ctx(readings=reading(temperature=26.9))) == []


def test_heater_refused_while_vent_is_open_and_vent_refused_while_heating():
    assert any("close it" in r for r in rules.check_command(heater(), ctx(readings=reading(temperature=17.0), vent_pos=60.0)))
    running = [rules.HistoryItem("heater", "run", {"duration_s": 900}, NOW)]
    assert any("heater is running" in r for r in rules.check_command(vent(50), ctx(history=running)))
    assert rules.check_command(vent(0), ctx(history=running)) == []            # closing is always allowed


def test_heater_data_gates_apply_to_run():
    assert rules.check_command(heater(), ctx(readings=reading(temperature=999.0)))


# ---------------------------------------------------------------- humidifier
def test_humidifier_runs_by_day_when_dry():
    assert rules.check_command(mist(), at_hour(10, readings=reading(humidity=45.0))) == []
    assert rules.check_command(mist(1801), at_hour(10, readings=reading(humidity=45.0)))


def test_humidifier_lockouts():
    dry = reading(humidity=45.0)
    assert any("daytime only" in r for r in rules.check_command(mist(), at_hour(22, readings=dry)))
    assert any("daytime only" in r for r in rules.check_command(mist(), at_hour(17, readings=dry)))     # 17:00 is outside 07-17
    assert any("limit" in r for r in rules.check_command(mist(), at_hour(10, readings=reading(humidity=80.0))))
    assert any("too cold" in r for r in rules.check_command(mist(), at_hour(10, readings=reading(humidity=45.0, temperature=14.0))))
    assert any("close it" in r for r in rules.check_command(mist(), at_hour(10, readings=dry, vent_pos=50.0)))


def test_vent_refused_while_misting():
    running = [rules.HistoryItem("humidifier", "run", {"duration_s": 900}, NOW)]
    assert any("humidifier is running" in r for r in rules.check_command(vent(50), ctx(history=running)))


# ---------------------------------------------------------------------- lamp
def test_lamp_is_a_winter_top_up_inside_its_photoperiod():
    j = lambda **kw: ctx(now=JAN, readings=reading(light=2000.0), **kw)           # noqa: E731
    assert rules.check_command(lamp(), j()) == []
    assert any("winter top-up" in r for r in rules.check_command(lamp(), ctx(readings=reading(light=2000.0))))      # June: refused
    assert any("photoperiod" in r for r in rules.check_command(lamp(), ctx(now=JAN.replace(hour=22), readings=reading(light=2000.0))))
    assert any("photoperiod" in r for r in rules.check_command(lamp(), ctx(now=JAN.replace(hour=6), readings=reading(light=2000.0))))


def test_lamp_refused_in_bright_light_or_when_the_day_is_already_bright_enough():
    assert any("bright enough" in r for r in rules.check_command(lamp(), ctx(now=JAN, readings=reading(light=30000.0))))
    assert any("upper limit" in r for r in rules.check_command(lamp(), ctx(now=JAN, readings=reading(light=2000.0), light_today_mol=20.0)))
    assert rules.check_command(lamp(), ctx(now=JAN, readings=reading(light=2000.0), light_today_mol=19.9)) == []


# --------------------------------------------------------------------- shade
def test_shade_dwell_and_always_allowed_to_open():
    recent = [rules.HistoryItem("shade", "set", {"position": 100}, NOW - timedelta(minutes=5))]
    assert any("shade moves" in r for r in rules.check_command(shade(100), ctx(history=recent)))
    assert rules.check_command(shade(0), ctx(history=recent)) == []
    assert rules.check_command(shade(100), ctx()) == []


# ----------------------------------------------------------- automatic control
def test_auto_vent_hysteresis_by_day():
    hot = auto("vent", ctx(readings=reading(temperature=32.0), vent_pos=0))
    assert hot == [{"device": "vent", "action": "set", "params": {"position": 100}}]
    assert auto("vent", ctx(readings=reading(temperature=26.0, humidity=70.0), vent_pos=100)) == []           # inside the band: hold
    assert auto("vent", ctx(readings=reading(temperature=22.0, humidity=70.0), vent_pos=60)) == [
        {"device": "vent", "action": "set", "params": {"position": 0}}]


def test_auto_night_cooling_is_small_steps_and_growing_season_only():
    night = lambda t, **kw: auto("vent", at_hour(23, readings=reading(temperature=t, humidity=70.0), **kw))     # noqa: E731
    # the night vent shares the heater's setpoint (17 C at 23:00): nothing between the setpoint and setpoint + 1 C (the dead band)
    assert night(17.0) == [] and night(17.9) == []
    assert night(18.5)[0]["params"]["position"] == 10                                    # just above the dead band: the smallest step
    step = night(21.0)
    assert step and 10 <= step[0]["params"]["position"] <= L.NIGHT_VENT_MAX_POS         # small step only
    assert night(30.0)[0]["params"]["position"] == L.NIGHT_VENT_MAX_POS                  # never opens far
    assert night(17.0, vent_pos=20.0) == [{"device": "vent", "action": "set", "params": {"position": 0}}]       # back in band: closes
    assert auto("vent", ctx(now=JAN.replace(hour=23), readings=reading(temperature=21.0, humidity=70.0), vent_pos=0)) == []   # rest: no night venting
    assert night(15.5) == []                                                              # too close to the floor to open (needs 3 C above it)


def test_auto_heating_is_proportional_with_a_day_and_a_night_setpoint():
    def duration(temp, hour=12, now=NOW):
        out = auto("heater", at_hour(hour, now, readings=reading(temperature=temp, soil_moisture=50.0, humidity=70.0)))
        return out[0]["params"]["duration_s"] if out else 0

    # day: setpoint 25 C, full power 3 C below it
    assert duration(25.0) == 0 and duration(26.0) == 0
    assert duration(24.0) == 600 and duration(23.0) == 1200 and duration(22.0) == 1800 and duration(10.0) == 1800
    # night, growing season: setpoint 17 C, full power at 15 C
    assert duration(17.0, hour=23) == 0 and duration(16.0, hour=23) == 900 and duration(15.0, hour=23) == 1800
    # night, winter rest: setpoint 12 C, full power at 10 C
    assert duration(12.0, hour=23, now=JAN) == 0 and duration(11.0, hour=23, now=JAN) == 900 and duration(10.0, hour=23, now=JAN) == 1800
    assert duration(24.99) == 0                                                                  # a shortfall worth under a minute is ignored


def test_auto_misting_is_proportional_daytime_only_and_needs_a_closed_vent():
    def duration(h, hour=10, vent_pos=0.0, temp=22.0):
        out = auto("humidifier", at_hour(hour, readings=reading(humidity=h, soil_moisture=50.0, temperature=temp), vent_pos=vent_pos))
        return out[0]["params"]["duration_s"] if out else 0

    assert duration(65.0) == 0 and duration(70.0) == 0
    assert duration(57.5) == 900 and duration(50.0) == 1800 and duration(30.0) == 1800
    assert duration(40.0, hour=22) == 0
    assert duration(40.0, vent_pos=60.0, temp=26.0) == 0       # vent held open in the 24-28 C band: no proposal
    assert duration(40.0, vent_pos=60.0, temp=22.0) == 1800    # the controller closes the vent in the same pass, so misting follows


def test_auto_irrigation_dry_down_by_season():
    dry = auto("pump", ctx(readings=reading(soil_moisture=20.0)))
    assert dry and dry[0]["params"]["duration_s"] <= 120
    assert not auto("doser", ctx(readings=reading(soil_moisture=20.0)))                  # feeding is manual: never proposed
    assert not auto("pump", ctx(readings=reading(soil_moisture=40.0)))                    # above the growing trigger of 30 %
    assert rules.auto_decide(ctx(readings=None)) == []
    assert not auto("pump", ctx(readings=reading(soil_moisture=20.0), history=[hist("pump", "run", 10 * H, duration_s=60)]))   # gap not met
    # winter rest: only when nearly dry, and no fertiliser
    assert not auto("pump", ctx(now=JAN, readings=reading(soil_moisture=20.0)))
    assert auto("pump", ctx(now=JAN, readings=reading(soil_moisture=10.0)))


def test_auto_grow_light_tops_up_winter_days_only():
    dark = reading(light=0.0, temperature=22.0, humidity=70.0, soil_moisture=50.0)
    morning = at_hour(8, JAN, readings=dark)
    full = auto("lamp", morning)
    assert full and full[0]["params"]["duration_s"] == L.LAMP_MAX_RUN_S                    # a dark winter morning: full power
    assert not auto("lamp", at_hour(8, NOW, readings=dark))                                # June: not a lamp month
    assert not auto("lamp", at_hour(22, JAN, readings=dark))                               # outside the photoperiod
    assert not auto("lamp", at_hour(12, JAN, readings=reading(light=31000.0, temperature=22.0, humidity=70.0)))   # bright sun
    assert not auto("lamp", at_hour(12, JAN, readings=dark, light_today_mol=L.DLI_TARGET))  # the day's target is already reached
    some = auto("lamp", at_hour(18, JAN, readings=reading(light=8100.0, temperature=22.0, humidity=70.0, soil_moisture=50.0), light_today_mol=14.0))
    assert not some or some[0]["params"]["duration_s"] < L.LAMP_MAX_RUN_S                  # nearly there: less than full power


def test_auto_shade_for_strong_summer_light_only():
    strong = reading(light=40000.0, temperature=22.0, humidity=70.0, soil_moisture=50.0)
    assert auto("shade", at_hour(12, NOW, readings=strong)) == [{"device": "shade", "action": "set", "params": {"position": 100}}]
    assert not auto("shade", at_hour(12, JAN, readings=strong))                            # winter: never shades
    assert auto("shade", at_hour(12, NOW, readings=reading(light=5000.0), shade_pos=100.0)) == [
        {"device": "shade", "action": "set", "params": {"position": 0}}]                     # weak light: opens again
    # the controller un-shades the reading: 20 klux measured through a closed shade is 40 klux outside, so it stays closed
    assert not auto("shade", at_hour(12, NOW, readings=reading(light=20000.0), shade_pos=100.0))


# ------------------------------------------------------------------ schedules
def sched(*hours, dur=60):
    return [{"due": NOW + timedelta(hours=h), "duration_s": dur} for h in hours]


def test_valid_schedule_passes():
    assert rules.validate_schedule(sched(1), ctx()) == []


def test_schedule_limits():
    assert any("minimum gap" in e for e in rules.validate_schedule(sched(1, 2), ctx()))
    assert any("more than" in e for e in rules.validate_schedule(sched(1, 5, 9), ctx()))
    assert rules.validate_schedule(sched(30), ctx())                 # beyond 24 h
    assert rules.validate_schedule(sched(1, dur=500), ctx())          # too long
    assert rules.validate_schedule(sched(-1), ctx())                  # overdue ...
    assert rules.validate_schedule(sched(-1), ctx(), allow_overdue=True) == []   # ... unless allowed at approval


def test_schedule_respects_history_and_the_seasonal_gap():
    assert any("minimum gap" in e for e in rules.validate_schedule(sched(1), ctx(history=[hist("pump", "run", 1 * H, duration_s=60)])))
    # an older watering still counts: the dry-down gap is 48 h in the growing season and 168 h in the rest
    assert any("minimum gap" in e for e in rules.validate_schedule(sched(1), ctx(history=[hist("pump", "run", 30 * H, duration_s=60)])))
    jan = [{"due": JAN + timedelta(hours=1), "duration_s": 60}]
    assert any("168" in e for e in rules.validate_schedule(jan, ctx(now=JAN, history=[hist("pump", "run", 4 * 24 * H, now=JAN, duration_s=60)])))


def test_shade_does_not_flip_at_dusk_or_dawn():
    # the shade holds outside the day window, so a fading evening light cannot make it open and close again
    assert not auto("shade", at_hour(19, NOW, readings=reading(light=7000.0), shade_pos=100.0))
    assert not auto("shade", at_hour(22, NOW, readings=reading(light=0.0), shade_pos=100.0))
    assert not auto("shade", at_hour(6, NOW, readings=reading(light=30000.0), shade_pos=0.0))
    assert auto("shade", at_hour(8, NOW, readings=reading(light=2000.0), shade_pos=100.0))     # but it does open in the day when the light is weak


# ------------------------------------------------------------- outdoor sensor
def out(**kw):
    return reading(**kw)


def test_day_vent_opens_only_if_the_outside_air_is_cooler():
    hot = dict(temperature=30.0, humidity=70.0, soil_moisture=50.0)
    assert auto("vent", ctx(readings=reading(**hot, outside_temperature=20.0), vent_pos=0))                    # cooler outside: opens
    assert not auto("vent", ctx(readings=reading(**hot, outside_temperature=29.0), vent_pos=0))                # only 1 C cooler: pointless
    assert not auto("vent", ctx(readings=reading(**hot, outside_temperature=33.0), vent_pos=0))                # warmer outside
    assert auto("vent", ctx(readings=reading(**hot), vent_pos=0))                                              # no outdoor reading: old rule


def test_night_cooling_is_sized_from_the_outdoor_reading():
    def night(t, outside, hour=23, now=NOW, **kw):
        c = at_hour(hour, now, readings=reading(temperature=t, humidity=70.0, soil_moisture=50.0, outside_temperature=outside), **kw)
        v = auto("vent", c)
        return v[0]["params"]["position"] if v else 0

    mild = night(21.0, 15.0)
    deep = night(24.0, 15.0)
    assert 10 <= mild <= L.NIGHT_VENT_SIZED_MAX_POS and 10 <= deep <= L.NIGHT_VENT_SIZED_MAX_POS
    assert night(21.0, 15.0) > night(21.0, 3.0)                                          # the colder the outside, the smaller the opening
    assert night(21.0, 19.0) == 0                                                        # outside only 2 C cooler: not worth it
    assert night(21.0, 22.0) == 0                                                        # outside warmer: stays shut
    assert night(17.0, 8.0, vent_pos=20.0) == 0                                          # back in the band: closes
    assert night(24.0, 5.0) <= L.VENT_COLD_OUTSIDE_MAX_POS                               # cold outside (< 15 C): never wide
    assert night(24.0, -5.0, hour=23, now=JAN) == 0                                      # winter rest: no night venting at all


def test_vent_may_not_open_wide_when_the_outside_air_is_below_the_safety_minimum():
    cold = ctx(readings=reading(temperature=24.0, outside_temperature=5.0))
    assert any("outside air" in r for r in rules.check_command(vent(40), cold))
    assert rules.check_command(vent(30), cold) == []
    assert rules.check_command(vent(40), ctx(readings=reading(temperature=24.0, outside_temperature=18.0))) == []   # mild outside: fine
    assert rules.check_command(vent(40), ctx(readings=reading(temperature=24.0))) == []                              # no outdoor reading: no rule


# ------------------------------------------------------------------- soak
def test_a_soak_continues_with_a_second_run_but_not_a_third_and_the_gap_still_applies():
    first = [hist("pump", "run", timedelta(minutes=30), duration_s=120)]                                  # 30 minutes ago: same watering
    assert rules.check_command(pump(60), ctx(readings=reading(soil_moisture=25.0), history=first)) == []   # continues the soak
    both = first + [hist("pump", "run", H, duration_s=120)]
    assert any("daily limit" in r for r in rules.check_command(pump(60), ctx(readings=reading(soil_moisture=25.0), history=both)))
    later = [hist("pump", "run", 3 * H, duration_s=120)]                                 # 3 h ago: a new watering, held by the 48 h gap
    assert any("minimum gap" in r for r in rules.check_command(pump(60), ctx(readings=reading(soil_moisture=25.0), history=later)))


def test_controller_soaks_in_two_runs_then_stops_at_the_target():
    dry = ctx(readings=reading(soil_moisture=8.0))
    assert [c["params"]["duration_s"] for c in rules.auto_decide(dry) if c["device"] == "pump"] == [120]
    mid = ctx(readings=reading(soil_moisture=25.0), history=[hist("pump", "run", timedelta(minutes=30), duration_s=120)])
    assert [c["params"]["duration_s"] for c in rules.auto_decide(mid) if c["device"] == "pump"] == [120]   # still below 60 %: keep soaking
    full = ctx(readings=reading(soil_moisture=60.0), history=[hist("pump", "run", timedelta(minutes=30), duration_s=120)])
    assert not [c for c in rules.auto_decide(full) if c["device"] == "pump"]                                # at the target: stop
    wet = ctx(readings=reading(soil_moisture=45.0))
    assert not [c for c in rules.auto_decide(wet) if c["device"] == "pump"]                                 # not dry enough to start a new soak
