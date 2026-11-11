"""Integration tests of the backend: REST API, MQTT handlers, controller, safety rules and assist.
MQTT publishing is replaced by a recording fake; the database is SQLite in memory."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient

from greenhouse import assist, bridge, controller
from greenhouse.models import ActuatorState, Command, EnvState, Reading, ScheduleItem, Suggestion

pytestmark = pytest.mark.django_db


class Recorder:
    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def __call__(self, topic, payload):
        self.sent.append((topic, payload))
        return self.ok


@pytest.fixture
def api():
    c = APIClient()
    c.defaults["HTTP_X_API_KEY"] = settings.API_KEY
    return c


@pytest.fixture
def anon():
    return APIClient()


_seq = iter(range(1, 10_000))


def telemetry(**kw):
    env = EnvState.get()
    p = {"sample_id": next(_seq), "sim_ts": env.sim_time.isoformat(), "soil_moisture": 50.0,
         "temperature": 22.0, "humidity": 60.0, "light": 40000.0}
    p.update(kw)
    return p


# ----------------------------------------------------------------- REST basics
def test_health_and_swagger(anon):
    assert anon.get("/api/v1/health/").status_code == 200
    schema = anon.get("/api/schema/")
    assert schema.status_code == 200 and b"/api/v1/readings/" in schema.content
    assert anon.get("/api/docs/").status_code == 200


def test_writes_need_the_api_key(anon, api):
    assert anon.post("/api/v1/actuators/pump/command/", {"action": "stop"}, format="json").status_code == 403
    assert api.post("/api/v1/actuators/pump/command/", {"action": "stop"}, format="json").status_code == 202
    assert api.post("/api/v1/actuators/toaster/command/", {"action": "run"}, format="json").status_code == 404
    assert api.post("/api/v1/actuators/heater/command/", {"action": "stop"}, format="json").status_code == 202
    assert anon.get("/api/v1/commands/").status_code == 403   # reads need a sign-in too


def test_generator_needs_its_own_token(anon):
    assert anon.post("/api/v1/sim/step/", {}, format="json").status_code == 403
    r1 = anon.post("/api/v1/sim/step/", {"minutes": 30}, format="json", HTTP_X_SIM_TOKEN=settings.SIM_TOKEN)
    r2 = anon.post("/api/v1/sim/step/", {"minutes": 30}, format="json", HTTP_X_SIM_TOKEN=settings.SIM_TOKEN)
    assert r1.status_code == 200 and r2.json()["sample_id"] == r1.json()["sample_id"] + 1
    assert set(r1.json()) == {"sample_id", "sim_ts", "soil_moisture", "temperature", "humidity", "light", "outside_temperature"}


def test_fault_injection_reaches_the_sample(api, anon):
    assert api.post("/api/v1/sim/fault/", {"metric": "temperature", "value": 99}, format="json").status_code == 200
    s = anon.post("/api/v1/sim/step/", {}, format="json", HTTP_X_SIM_TOKEN=settings.SIM_TOKEN).json()
    assert s["temperature"] == 99.0
    assert api.delete("/api/v1/sim/fault/").status_code == 204


def test_temperature_fault_is_capped_at_100_c(api):
    assert api.post("/api/v1/sim/fault/", {"metric": "temperature", "value": 999}, format="json").status_code == 400
    assert api.post("/api/v1/sim/fault/", {"metric": "temperature", "value": -101}, format="json").status_code == 400
    assert api.post("/api/v1/sim/fault/", {"metric": "temperature", "value": 100}, format="json").status_code == 200


# ------------------------------------------------- telemetry -> auto control
def test_dry_soil_triggers_checked_irrigation_but_never_feeds():
    out = Recorder()
    bridge.handle_telemetry(telemetry(soil_moisture=20.0), out)
    devices = [t.split("/")[3] for t, _ in out.sent]
    assert "pump" in devices and "doser" not in devices        # feeding is manual
    pump_cmd = next(p for t, p in out.sent if "/pump/" in t)
    assert 1 <= pump_cmd["duration_s"] <= 120 and "issued_at" in pump_cmd
    assert Command.objects.filter(status="accepted", source="auto", device="pump").count() == 1


def test_cool_greenhouse_gets_proportional_heating_and_the_vent_stays_shut():
    out = Recorder()
    bridge.handle_telemetry(telemetry(temperature=16.0), out)        # the simulated clock starts at 00:00: night, growing season
    heater = next(p for t, p in out.sent if "/heater/" in t)
    assert heater["duration_s"] == 900                        # (17 - 16) / 2 of a 1800 s interval
    assert not any("/vent/" in t and p.get("position", 0) > 0 for t, p in out.sent)


def test_dry_air_by_day_triggers_misting_but_not_at_night():
    night = Recorder()
    bridge.handle_telemetry(telemetry(humidity=40.0), night)           # simulated clock starts at 00:00
    assert not any("/humidifier/" in t for t, _ in night.sent)
    env = EnvState.get()
    env.sim_time += timedelta(hours=10)
    env.save()
    day = Recorder()
    bridge.handle_telemetry(telemetry(humidity=40.0), day)
    mist = next(p for t, p in day.sent if "/humidifier/" in t)
    assert mist["duration_s"] == 1800                                   # (65 - 40) / 15 is capped at the full interval


def test_humidifier_events_change_the_simulated_greenhouse():
    bridge.handle_actuator_state("humidifier", {"state": "idle", "event": "run_complete", "ran_s": 600, "cmd_id": 12})
    assert EnvState.get().pending_humidifier_s == 600


def test_feeding_is_manual_and_goes_through_the_interlock(api):
    out = Recorder()
    bridge.handle_telemetry(telemetry(soil_moisture=50.0), out)
    body = api.post("/api/v1/feed/", {"duration_s": 60, "volume_ml": 20}, format="json")
    assert body.status_code == 202 and [c["device"] for c in body.json()] == ["pump", "doser"]
    controller.dispatch_queued(out)
    assert [c.status for c in Command.objects.filter(source="manual").order_by("id")] == ["accepted", "accepted"]   # the dose sees the pump running
    assert any("/doser/" in t for t, _ in out.sent)
    assert api.post("/api/v1/feed/", {"duration_s": 60}, format="json").status_code == 400                        # needs both values


def test_no_feeding_in_the_winter_rest(api):
    env = EnvState.get()
    env.sim_time = env.sim_time.replace(year=2026, month=1, day=10, hour=12)
    env.save()
    bridge.handle_telemetry(telemetry(soil_moisture=35.0), Recorder())
    api.post("/api/v1/feed/", {"duration_s": 30, "volume_ml": 10}, format="json")
    controller.dispatch_queued(Recorder())
    pump, dose = Command.objects.filter(source="manual").order_by("id")
    assert pump.status == "accepted" and dose.status == "rejected" and "winter rest" in dose.reasons[0]


def test_lamp_and_shade_events_change_the_simulated_greenhouse():
    bridge.handle_actuator_state("lamp", {"state": "idle", "event": "run_complete", "ran_s": 1200, "cmd_id": 21})
    bridge.handle_actuator_state("shade", {"state": "open", "event": "moved", "position": 100, "cmd_id": 22})
    e = EnvState.get()
    assert e.pending_lamp_s == 1200 and e.shade_pos == 100


def test_heater_events_change_the_simulated_greenhouse():
    bridge.handle_actuator_state("heater", {"state": "idle", "event": "run_complete", "ran_s": 900, "cmd_id": 11})
    assert EnvState.get().pending_heater_s == 900


def test_sim_step_records_the_outside_weather(anon, api):
    anon.post("/api/v1/sim/step/", {}, format="json", HTTP_X_SIM_TOKEN=settings.SIM_TOKEN)
    w = api.get("/api/v1/weather/").json()["results"]
    assert len(w) == 1 and -20 < w[0]["temperature"] < 40 and 0 <= w[0]["humidity"] <= 100


def test_a_soak_is_two_runs_and_a_third_inside_the_day_is_refused_and_logged(api):
    out = Recorder()
    bridge.handle_telemetry(telemetry(soil_moisture=20.0), out)         # the controller starts a soak
    bridge.handle_telemetry(telemetry(soil_moisture=20.0), out)         # the soak continues with a second run
    assert len([1 for t, _ in out.sent if "/pump/" in t]) == 2
    bridge.handle_telemetry(telemetry(soil_moisture=20.0), out)         # the daily limit is reached: the controller proposes nothing more
    assert len([1 for t, _ in out.sent if "/pump/" in t]) == 2
    cid = api.post("/api/v1/actuators/pump/command/", {"action": "run", "params": {"duration_s": 30}}, format="json").json()["id"]
    controller.dispatch_queued(out)                                       # a manual request is checked and refused
    cmd = Command.objects.get(pk=cid)
    assert cmd.status == "rejected" and any("daily limit" in r for r in cmd.reasons)
    assert len([1 for t, _ in out.sent if "/pump/" in t]) == 2


def test_implausible_sensor_value_inhibits_actuation():
    out = Recorder()
    r = bridge.handle_telemetry(telemetry(temperature=75.0, soil_moisture=20.0), out)
    assert r.plausible is False
    assert out.sent == []
    assert Command.objects.filter(status="rejected").exists()


def test_duplicate_and_malformed_telemetry_ignored():
    out = Recorder()
    p = telemetry()
    assert bridge.handle_telemetry(p, out) is not None
    assert bridge.handle_telemetry(p, out) is None
    assert bridge.handle_telemetry({"sample_id": 5}, out) is None
    assert Reading.objects.count() == 1


def test_stale_data_blocks_manual_command(api):
    bridge.handle_telemetry(telemetry(soil_moisture=20.0), Recorder())
    Reading.objects.update(ts=timezone.now() - timedelta(days=1))
    cid = api.post("/api/v1/actuators/pump/command/", {"action": "run", "params": {"duration_s": 30}}, format="json").json()["id"]
    out = Recorder()
    controller.dispatch_queued(out)
    cmd = Command.objects.get(pk=cid)
    assert cmd.status == "rejected" and "stale" in cmd.reasons[0] and out.sent == []


def test_broker_failure_fails_safe(api):
    bridge.handle_telemetry(telemetry(soil_moisture=20.0), Recorder(ok=False))
    assert not Command.objects.filter(status="accepted").exists()
    assert Command.objects.filter(status="rejected").exists()


def test_manual_command_outcome_visible_over_rest(api):
    bridge.handle_telemetry(telemetry(soil_moisture=65.0), Recorder())
    cid = api.post("/api/v1/actuators/pump/command/", {"action": "run", "params": {"duration_s": 30}}, format="json").json()["id"]
    out = Recorder()
    controller.dispatch_queued(out)
    body = api.get(f"/api/v1/commands/{cid}/").json()
    assert body["status"] == "rejected" and "target" in body["reasons"][0] and out.sent == []


# ----------------------------------------------- actuator feedback -> simulator
def test_actuator_events_change_the_simulated_greenhouse_once():
    ev = {"state": "idle", "event": "run_complete", "ran_s": 100, "cmd_id": 7}
    bridge.handle_actuator_state("pump", ev)
    bridge.handle_actuator_state("pump", ev)           # retained message replayed on reconnect
    assert EnvState.get().pending_pump_s == 100
    bridge.handle_actuator_state("vent", {"state": "open", "event": "moved", "position": 60, "cmd_id": 8})
    assert EnvState.get().vent_pos == 60
    bridge.handle_actuator_state("doser", {"state": "idle", "event": "dose_complete", "dosed_ml": 20, "cmd_id": 9})
    assert EnvState.get().pending_dose_ml == 20
    assert ActuatorState.objects.get(device="pump").state == "idle"


def test_pump_run_started_does_not_block_its_completion_event():
    bridge.handle_actuator_state("pump", {"state": "running", "event": "run_started", "cmd_id": 3})
    bridge.handle_actuator_state("pump", {"state": "idle", "event": "run_complete", "ran_s": 60, "cmd_id": 3})
    assert EnvState.get().pending_pump_s == 60


def test_actuator_refusal_marks_command_rejected():
    c = Command.objects.create(source="auto", device="pump", action="run", params={"duration_s": 30}, status="accepted")
    bridge.handle_actuator_state("pump", {"state": "unchanged", "event": "rejected", "reason": "interlock", "cmd_id": c.id})
    c.refresh_from_db()
    assert c.status == "rejected" and "interlock" in c.reasons[0]


# ------------------------------------------------------------------- assist
def test_explain_in_mock_mode(anon, api):
    assert api.get("/api/v1/assist/explain/").status_code == 503      # no readings yet: fails cleanly
    bridge.handle_telemetry(telemetry(), Recorder())
    r = api.get("/api/v1/assist/explain/")
    assert r.status_code == 200 and r.json()["mode"] == "mock" and r.json()["explanation"]


def test_suggestion_approval_and_scheduled_execution(api):
    bridge.handle_telemetry(telemetry(soil_moisture=35.0), Recorder())     # 35%: above auto trigger, below 45% mock trigger
    sug = api.post("/api/v1/assist/suggestions/").json()
    assert sug["status"] == "pending_review" and len(sug["items"]) == 1
    assert api.post(f"/api/v1/assist/suggestions/{sug['id']}/approve/").json()["status"] == "approved"
    out = Recorder()
    assert controller.run_schedule(out) == 0                              # not due yet
    env = EnvState.get()
    env.sim_time += timedelta(hours=2)
    env.save()
    assert controller.run_schedule(out) == 1                              # due: executed through the rules engine
    cmd = Command.objects.get(source="schedule")
    assert cmd.status == "accepted" and ScheduleItem.objects.get().status == "submitted"


def test_unsafe_schedule_from_the_model_is_rejected_by_safety(api, monkeypatch):
    bridge.handle_telemetry(telemetry(), Recorder())
    bad = {"mode": "claude", "model": "x", "explanation": "water a lot",
           "items": [{"at_offset_hours": h, "duration_s": 120, "reason": "r"} for h in (1, 2, 3, 4, 5)]}
    monkeypatch.setattr(assist, "suggest", lambda: bad)
    sug = api.post("/api/v1/assist/suggestions/").json()
    assert sug["status"] == "rejected_by_safety" and sug["violations"]
    assert api.post(f"/api/v1/assist/suggestions/{sug['id']}/approve/").status_code == 409


def test_human_can_reject_a_suggestion(api):
    bridge.handle_telemetry(telemetry(soil_moisture=35.0), Recorder())
    sug = api.post("/api/v1/assist/suggestions/").json()
    assert api.post(f"/api/v1/assist/suggestions/{sug['id']}/reject/").json()["status"] == "rejected"
    assert Suggestion.objects.get().decided_at is not None


class FakeClaude:
    def __init__(self, content, stop_reason="tool_use"):
        self.calls = []
        self.messages = self
        self._resp = SimpleNamespace(content=content, stop_reason=stop_reason)

    def create(self, **kw):
        self.calls.append(kw)
        return self._resp


def test_claude_path_uses_strict_tool_without_forced_tool_choice(anon, api, monkeypatch, settings):
    settings.ASSIST_MODE = "claude"
    bridge.handle_telemetry(telemetry(soil_moisture=35.0), Recorder())
    tool_use = SimpleNamespace(type="tool_use", name="propose_watering_schedule", input={
        "explanation": "Moisture is falling.", "items": [{"at_offset_hours": 2, "duration_s": 50, "reason": "dry"}]})
    # SimpleNamespace(name=...) is fine here; .name is a plain attribute
    fake = FakeClaude([tool_use])
    monkeypatch.setattr(assist, "_client", lambda: fake)
    sug = api.post("/api/v1/assist/suggestions/").json()
    assert sug["status"] == "pending_review" and sug["mode"] == "claude" and sug["model"] == settings.ASSIST_MODEL
    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-5-5" and "tool_choice" not in call and call["tools"][0]["strict"] is True
    assert "<data>" in call["messages"][0]["content"]                       # data is delimited as data


def test_claude_failures_degrade_gracefully(anon, api, monkeypatch, settings):
    settings.ASSIST_MODE = "claude"
    bridge.handle_telemetry(telemetry(), Recorder())

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(assist, "_client", boom)
    assert api.get("/api/v1/assist/explain/").status_code == 503
    monkeypatch.setattr(assist, "_client", lambda: FakeClaude([], stop_reason="refusal"))
    assert api.get("/api/v1/assist/explain/").status_code == 503
    monkeypatch.setattr(assist, "_client", lambda: FakeClaude([SimpleNamespace(type="text", text="no tool")]))
    assert api.get("/api/v1/assist/explain/").status_code == 200           # explain only needs text
    assert APIClient().get("/api/v1/health/").status_code == 200            # rest of the system unaffected


def test_outdoor_temperature_is_stored_and_an_implausible_value_is_ignored():
    r = bridge.handle_telemetry(telemetry(outside_temperature=7.5), Recorder())
    assert r.outside_temperature == 7.5
    assert controller.build_context().readings["outside_temperature"] == 7.5
    bridge.handle_telemetry(telemetry(outside_temperature=500.0), Recorder())            # a faulty outdoor sensor
    assert "outside_temperature" not in controller.build_context().readings              # ignored: the controller falls back
    bridge.handle_telemetry(telemetry(), Recorder())                                     # no outdoor sensor at all
    assert Reading.objects.order_by("-id").first().outside_temperature is None


def test_cold_outside_air_caps_a_manual_vent_command(api):
    bridge.handle_telemetry(telemetry(temperature=24.0, outside_temperature=3.0), Recorder())
    cid = api.post("/api/v1/actuators/vent/command/", {"action": "set", "params": {"position": 60}}, format="json").json()["id"]
    controller.dispatch_queued(Recorder())
    cmd = Command.objects.get(pk=cid)
    assert cmd.status == "rejected" and "outside air" in cmd.reasons[0]
