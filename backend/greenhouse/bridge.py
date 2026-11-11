"""Handlers for inbound MQTT messages (kept free of paho so they can be unit-tested).

telemetry      -> stored as a Reading, then automatic control runs
actuator state -> stored, and the reported effect is applied to the simulated greenhouse
status         -> stored as DeviceStatus (online/offline from Last Will)
"""
from __future__ import annotations

import logging
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from greengrow_common import topics

from . import controller, rules
from .models import ActuatorState, Command, DeviceStatus, EnvState, Reading

log = logging.getLogger("greengrow.bridge")
REQUIRED = ("sample_id", "sim_ts", "soil_moisture", "temperature", "humidity", "light")


def handle_message(topic: str, payload: dict, publish) -> None:
    if topic.endswith("/sensors/telemetry"):
        handle_telemetry(payload, publish)
    elif topic.endswith("/state"):
        handle_actuator_state(topics.device_from_topic(topic), payload)
    elif topic.endswith("/edge/status"):
        _set_status("edge-node", payload)
    elif topic.endswith("/actuators/status"):
        _set_status("actuators", payload)


def _set_status(name: str, payload: dict) -> None:
    DeviceStatus.objects.update_or_create(name=name, defaults={"online": bool(payload.get("online"))})


def handle_telemetry(payload: dict, publish) -> Reading | None:
    if any(k not in payload for k in REQUIRED):
        log.warning("telemetry missing fields, dropped: %s", payload)
        return None
    if Reading.objects.filter(sample_id=payload["sample_id"]).exists():
        return None  # QoS 1 duplicate
    values = {k: payload[k] for k in ("soil_moisture", "temperature", "humidity", "light")}
    try:
        sim_ts = datetime.fromisoformat(payload["sim_ts"])
        plausible = not rules.plausibility(values)
        outside = payload.get("outside_temperature")
        outside = float(outside) if isinstance(outside, (int, float)) and not isinstance(outside, bool) else None
        reading = Reading.objects.create(sample_id=payload["sample_id"], sim_ts=sim_ts, plausible=plausible, outside_temperature=outside, **values)
    except (TypeError, ValueError) as exc:
        log.warning("telemetry rejected (%s): %s", exc, payload)
        return None
    controller.run_auto(publish)
    controller.run_schedule(publish)
    return reading


@transaction.atomic
def handle_actuator_state(device: str, payload: dict) -> None:
    if device not in topics.DEVICES:
        return
    obj, _ = ActuatorState.objects.select_for_update().get_or_create(device=device)
    obj.state = str(payload.get("state", "unknown"))[:20]
    obj.detail = payload
    cmd_id, event = payload.get("cmd_id"), payload.get("event")
    # Retained messages are replayed on reconnect: apply each command's effect exactly once.
    key = f"{cmd_id}:{event}"
    if cmd_id is not None and event and obj.last_applied != key:
        obj.last_applied = key
        _apply_event(device, event, payload, cmd_id)
    obj.save()


def _apply_event(device: str, event: str, p: dict, cmd_id: int) -> None:
    env = EnvState.get(lock=True)
    if event in ("run_complete", "stopped") and device == "pump":
        env.pending_pump_s += float(p.get("ran_s", 0))
    elif event in ("run_complete", "stopped") and device == "heater":
        env.pending_heater_s += float(p.get("ran_s", 0))
    elif event in ("run_complete", "stopped") and device == "lamp":
        env.pending_lamp_s += float(p.get("ran_s", 0))
    elif event == "moved" and device == "shade":
        env.shade_pos = float(p.get("position", env.shade_pos))
    elif event in ("run_complete", "stopped") and device == "humidifier":
        env.pending_humidifier_s += float(p.get("ran_s", 0))
    elif event == "dose_complete" and device == "doser":
        env.pending_dose_ml += float(p.get("dosed_ml", 0))
    elif event == "moved" and device == "vent":
        env.vent_pos = float(p.get("position", env.vent_pos))
    elif event == "rejected":
        Command.objects.filter(pk=cmd_id, status="accepted").update(
            status="rejected", reasons=["actuator refused: " + str(p.get("reason", "unspecified"))], decided_at=timezone.now()
        )
        return
    else:
        return
    env.save()
