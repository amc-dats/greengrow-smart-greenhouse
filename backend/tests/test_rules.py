from datetime import datetime, timedelta, timezone

from greenhouse import rules

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)


def ctx(**kw):
    base = dict(
        now=NOW,
        readings={"soil_moisture": 25.0, "temperature": 22.0, "humidity": 60.0, "light": 50000.0},
        reading_age_s=5.0, max_age_s=3600.0, history=[], vent_pos=0.0,
    )
    base.update(kw)
    return rules.Context(**base)


def reading(**kw):
    r = {"soil_moisture": 25.0, "temperature": 22.0, "humidity": 60.0, "light": 50000.0}
    r.update(kw)
    return r


def pump(d=60):
    return {"device": "pump", "action": "run", "params": {"duration_s": d}}


def vent(p):
    return {"device": "vent", "action": "set", "params": {"position": p}}


def dose(v=20):
    return {"device": "doser", "action": "dose", "params": {"volume_ml": v}}


def hist(device, action, ago, **params):
    return rules.HistoryItem(device, action, params, NOW - ago)


# ------------------------------------------------------------------ structure
def test_valid_pump_run_is_allowed():
    assert rules.check_command(pump(60), ctx()) == []


def test_unknown_device_and_action():
    assert rules.check_command({"device": "heater", "action": "run", "params": {}}, ctx())
    assert rules.check_command({"device": "pump", "action": "dose", "params": {}}, ctx())


def test_parameter_bounds_and_types():
    assert rules.check_command(pump(121), ctx())      # above hard maximum
    assert rules.check_command(pump(0), ctx())
    assert rules.check_command(pump(60.5), ctx())     # not a whole number
    assert rules.check_command(pump(True), ctx())     # bool is not a number here
    assert rules.check_command(vent(101), ctx())
    assert rules.check_command(dose(51), ctx())
    assert rules.check_command({"device": "pump", "action": "run", "params": {"duration_s": 30, "speed": 9}}, ctx())


# ---------------------------------------------------------------- data gates
def test_no_data_stale_and_implausible_block_actuation_but_not_stop():
    assert rules.check_command(pump(), ctx(readings=None))
    assert rules.check_command(pump(), ctx(reading_age_s=4000.0))
    assert rules.check_command(pump(), ctx(reading_age_s=None))
    assert rules.check_command(pump(), ctx(readings=reading(temperature=999.0)))
    assert rules.check_command(pump(), ctx(readings=reading(soil_moisture=float("nan"))))
    stop = {"device": "pump", "action": "stop", "params": {}}
    assert rules.check_command(stop, ctx(readings=None)) == []        # safe direction always allowed
    assert rules.check_command(vent(0), ctx(reading_age_s=9999.0)) == []


# ---------------------------------------------------------------------- pump
def test_pump_refused_when_moisture_at_target_or_ceiling_would_be_exceeded():
    assert rules.check_command(pump(30), ctx(readings=reading(soil_moisture=60.0)))
    assert rules.check_command(pump(120), ctx(readings=reading(soil_moisture=55.0)))   # 55 + 18 > 70
    assert rules.check_command(pump(30), ctx(readings=reading(soil_moisture=55.0))) == []


def test_pump_daily_limit_and_minimum_gap():
    four = [hist("pump", "run", ago, duration_s=60) for ago in (23 * H, 17 * H, 11 * H, 5 * H)]
    reasons = rules.check_command(pump(), ctx(history=four))
    assert any("daily limit" in r for r in reasons)
    reasons = rules.check_command(pump(), ctx(history=[hist("pump", "run", 1 * H, duration_s=60)]))
    assert any("minimum gap" in r for r in reasons)
    assert rules.check_command(pump(), ctx(history=[hist("pump", "run", 4 * H, duration_s=60)])) == []


# ---------------------------------------------------------------------- vent
def test_vent_cold_protection_blocks_opening_not_closing():
    cold = ctx(readings=reading(temperature=14.0))            # below the 15 C safety minimum
    assert any("cold protection" in r for r in rules.check_command(vent(50), cold))
    assert rules.check_command(vent(0), cold) == []
    assert rules.check_command(vent(50), ctx(readings=reading(temperature=15.0))) == []


# ---------------------------------------------------------------- humidifier
def mist(d=900):
    return {"device": "humidifier", "action": "run", "params": {"duration_s": d}}


def at_hour(h, **kw):
    return ctx(now=NOW.replace(hour=h), **kw)


def test_humidifier_runs_by_day_when_dry():
    assert rules.check_command(mist(), at_hour(10, readings=reading(humidity=45.0))) == []
    assert rules.check_command(mist(1801), at_hour(10, readings=reading(humidity=45.0)))      # over the hard maximum


def test_humidifier_lockouts():
    dry = reading(humidity=45.0)
    assert any("daytime only" in r for r in rules.check_command(mist(), at_hour(22, readings=dry)))
    assert any("daytime only" in r for r in rules.check_command(mist(), at_hour(17, readings=dry)))     # 17:00 is outside 07-17
    assert any("limit" in r for r in rules.check_command(mist(), at_hour(10, readings=reading(humidity=80.0))))
    assert any("too cold" in r for r in rules.check_command(mist(), at_hour(10, readings=reading(humidity=45.0, temperature=14.0))))
    assert any("close it" in r for r in rules.check_command(mist(), at_hour(10, readings=dry, vent_pos=50.0)))


def test_humidifier_stop_always_allowed_and_vent_refused_while_misting():
    stop = {"device": "humidifier", "action": "stop", "params": {}}
    assert rules.check_command(stop, ctx(readings=None)) == []
    running = [rules.HistoryItem("humidifier", "run", {"duration_s": 900}, NOW)]
    assert any("humidifier is running" in r for r in rules.check_command(vent(50), ctx(history=running)))


def test_auto_misting_is_proportional_daytime_only_and_needs_a_closed_vent():
    def duration(h, hour=10, vent_pos=0.0, temp=22.0):
        out = [c for c in rules.auto_decide(at_hour(hour, readings=reading(humidity=h, soil_moisture=50.0, temperature=temp), vent_pos=vent_pos))
               if c["device"] == "humidifier"]
        return out[0]["params"]["duration_s"] if out else 0

    assert duration(65.0) == 0 and duration(70.0) == 0         # at or above the target: no misting
    assert duration(57.5) == 900 and duration(50.0) == 1800    # half, then full, of the 1800 s interval
    assert duration(30.0) == 1800                              # capped at one interval
    assert duration(40.0, hour=22) == 0                        # not at night
    assert duration(40.0, vent_pos=60.0, temp=26.0) == 0       # vent held open in the 24-28 C band: no proposal (nothing refused and logged)
    assert duration(40.0, vent_pos=60.0, temp=22.0) == 1800    # but if the controller closes the vent in the same pass, misting follows it


# -------------------------------------------------------------------- heater
def heater(d=900):
    return {"device": "heater", "action": "run", "params": {"duration_s": d}}


def test_heater_runs_when_cool_and_is_bounded():
    assert rules.check_command(heater(900), ctx(readings=reading(temperature=17.0))) == []
    assert rules.check_command(heater(1801), ctx(readings=reading(temperature=17.0)))       # over the hard maximum
    assert any("heating limit" in r for r in rules.check_command(heater(), ctx(readings=reading(temperature=22.0))))


def test_heater_refused_while_vent_is_open_and_vent_refused_while_heating():
    assert any("close it" in r for r in rules.check_command(heater(), ctx(readings=reading(temperature=17.0), vent_pos=60.0)))
    running = [rules.HistoryItem("heater", "run", {"duration_s": 900}, NOW)]
    assert any("heater is running" in r for r in rules.check_command(vent(50), ctx(history=running)))
    assert rules.check_command(vent(0), ctx(history=running)) == []            # closing is always allowed


def test_heater_stop_always_allowed_and_data_gates_apply_to_run():
    stop = {"device": "heater", "action": "stop", "params": {}}
    assert rules.check_command(stop, ctx(readings=None)) == []
    assert rules.check_command(heater(), ctx(readings=reading(temperature=999.0)))


def test_vent_minimum_dwell():
    recent = [rules.HistoryItem("vent", "set", {"position": 100}, NOW - timedelta(minutes=5))]
    assert rules.check_command(vent(50), ctx(history=recent))
    assert rules.check_command(vent(50), ctx(history=[rules.HistoryItem("vent", "set", {"position": 100}, NOW - timedelta(minutes=30))])) == []


# --------------------------------------------------------------------- doser
def test_doser_only_while_pump_running():
    assert any("only allowed while" in r for r in rules.check_command(dose(), ctx()))
    running = [rules.HistoryItem("pump", "run", {"duration_s": 60}, NOW)]
    assert rules.check_command(dose(), ctx(history=running)) == []
    finished = [rules.HistoryItem("pump", "run", {"duration_s": 60}, NOW - timedelta(minutes=30))]
    assert rules.check_command(dose(), ctx(history=finished))


def test_doser_daily_limit_and_gap():
    running = rules.HistoryItem("pump", "run", {"duration_s": 60}, NOW)
    four = [hist("doser", "dose", ago, volume_ml=50) for ago in (13 * H, 17 * H, 20 * H, 23 * H)]
    assert any("daily nutrient" in r for r in rules.check_command(dose(20), ctx(history=[running] + four)))
    recent = [running, hist("doser", "dose", 2 * H, volume_ml=20)]
    assert any("minimum gap" in r for r in rules.check_command(dose(20), ctx(history=recent)))


# ----------------------------------------------------------- automatic control
def test_auto_vent_hysteresis():
    hot = rules.auto_decide(ctx(readings=reading(temperature=32.0), vent_pos=0))
    assert {"device": "vent", "action": "set", "params": {"position": 100}} in hot
    assert rules.auto_decide(ctx(readings=reading(temperature=26.0, soil_moisture=50.0, humidity=70.0), vent_pos=100)) == []  # inside the band: hold
    cooled = rules.auto_decide(ctx(readings=reading(temperature=22.0, soil_moisture=50.0, humidity=70.0), vent_pos=60))
    assert cooled == [{"device": "vent", "action": "set", "params": {"position": 0}}]
    cold = rules.auto_decide(ctx(readings=reading(temperature=5.0, soil_moisture=50.0, humidity=70.0), vent_pos=40))
    assert cold[0] == {"device": "vent", "action": "set", "params": {"position": 0}}   # vent closes first ...
    assert cold[1] == {"device": "heater", "action": "run", "params": {"duration_s": 1800}}   # ... then full heating


def test_auto_heating_is_proportional_to_the_shortfall():
    def duration(temp):
        out = [c for c in rules.auto_decide(ctx(readings=reading(temperature=temp, soil_moisture=50.0))) if c["device"] == "heater"]
        return out[0]["params"]["duration_s"] if out else 0

    assert duration(20.0) == 0 and duration(21.0) == 0       # at or above the setpoint: no heating
    assert duration(19.0) == 450 and duration(18.0) == 900   # a quarter, then half of the 1800 s interval
    assert duration(16.0) == 1800 and duration(10.0) == 1800 # full heating at the bottom of the band
    assert duration(19.9) == 0                               # shortfalls worth under one minute of heating are ignored


def test_auto_irrigation_trigger_and_sizing():
    dry = rules.auto_decide(ctx(readings=reading(soil_moisture=45.0 - 20)))
    pumps = [c for c in dry if c["device"] == "pump"]
    assert pumps and pumps[0]["params"]["duration_s"] <= 120
    assert any(c["device"] == "doser" for c in dry)
    assert not [c for c in rules.auto_decide(ctx(readings=reading(soil_moisture=40.0))) if c["device"] == "pump"]
    assert rules.auto_decide(ctx(readings=None)) == []


# ------------------------------------------------------------------ schedules
def sched(*hours, dur=60):
    return [{"due": NOW + timedelta(hours=h), "duration_s": dur} for h in hours]


def test_valid_schedule_passes():
    assert rules.validate_schedule(sched(1, 7), ctx()) == []


def test_schedule_limits():
    assert any("minimum gap" in e for e in rules.validate_schedule(sched(1, 2), ctx()))
    assert any("more than" in e for e in rules.validate_schedule(sched(1, 5, 9, 13, 17), ctx()))
    assert rules.validate_schedule(sched(30), ctx())                 # beyond 24 h
    assert rules.validate_schedule(sched(1, dur=500), ctx())          # too long
    assert rules.validate_schedule(sched(-1), ctx())                  # overdue ...
    assert rules.validate_schedule(sched(-1), ctx(), allow_overdue=True) == []   # ... unless allowed at approval


def test_schedule_respects_history():
    recent = [hist("pump", "run", 1 * H, duration_s=60)]
    assert any("minimum gap" in e for e in rules.validate_schedule(sched(1), ctx(history=recent)))
