"""Simulated actuator node (ACT): irrigation pump, vent motor, nutrient dosing pump, heater, humidifier (fogger), grow light, shade.

This node is the last line of defence and does NOT trust the backend. It re-checks every command
against hard limits taken from safety_limits (command shape, maximum run time and dose, stale-command
expiry, doser interlocked to the pump) and fails safe: if the broker connection is lost, anything running
(pump, heater) is stopped. The backend's rules engine is the first layer; these checks are an independent
second layer.

SIM_SPEED compresses simulated time: a timed run of N simulated seconds takes max(N / SIM_SPEED, 2) real seconds.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from greengrow_common import mqtt as gmqtt
from greengrow_common import safety_limits as L
from greengrow_common import topics

log = logging.getLogger("greengrow.actuators")
MIN_REAL_RUN_S = 2.0
TIMED_MAX_S = {  # devices that run for a duration
    "pump": L.PUMP_MAX_RUN_S,
    "heater": L.HEATER_MAX_RUN_S,
    "humidifier": L.HUMIDIFIER_MAX_RUN_S,
    "lamp": L.LAMP_MAX_RUN_S,
}


RETRIGGERABLE = {"heater", "humidifier", "lamp"}  # continuous-duty devices that are re-commanded each interval


POSITION_DEVICES = {"vent", "shade"}  # devices set to a position 0-100


def _whole(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and v == int(v)


def hard_check(device: str, cmd: dict) -> str | None:
    """Return a reason string if the command violates a hard limit, else None."""
    action = cmd.get("action")
    if device in TIMED_MAX_S:
        if action == "stop":
            return None
        d = cmd.get("duration_s")
        if action != "run" or not _whole(d):
            return f"{device} accepts 'run' with whole duration_s, or 'stop'"
        if not 1 <= d <= TIMED_MAX_S[device]:
            return f"duration_s {d} outside 1..{TIMED_MAX_S[device]}"
    elif device in POSITION_DEVICES:
        p = cmd.get("position")
        if action != "set" or not _whole(p):
            return f"{device} accepts 'set' with whole position"
        if not 0 <= p <= 100:
            return f"position {p} outside 0..100"
    elif device == "doser":
        v = cmd.get("volume_ml")
        if action != "dose" or not _whole(v):
            return "doser accepts 'dose' with whole volume_ml"
        if not 1 <= v <= L.DOSE_MAX_ML:
            return f"volume_ml {v} outside 1..{L.DOSE_MAX_ML}"
    else:
        return f"unknown device '{device}'"
    return None


class TimedRun:
    """A device that runs for a duration (pump, heater)."""

    def __init__(self):
        self.timer = None
        self.cmd_id = None
        self.started = 0.0
        self.sim_s = 0

    @property
    def running(self) -> bool:
        return self.timer is not None


class ActuatorNode:
    def __init__(self, publish, sim_speed: float = 1.0, now=time.time, timer_cls=threading.Timer):
        self.publish = publish  # publish(device, payload_dict) -> None
        self.sim_speed = sim_speed
        self.now = now
        self.timer_cls = timer_cls
        self.lock = threading.RLock()
        self.timed = {name: TimedRun() for name in TIMED_MAX_S}
        self.positions = {name: 0 for name in POSITION_DEVICES}

    # ------------------------------------------------------------------ input
    def handle_command(self, device: str, cmd: dict) -> None:
        cmd_id = cmd.get("cmd_id")
        with self.lock:
            age = self.now() - float(cmd.get("issued_at", 0) or 0)
            if age > L.COMMAND_TTL_S or age < -5:
                return self._reject(device, cmd_id, f"command expired or from the future (age {age:.0f}s)")
            reason = hard_check(device, cmd)
            if reason:
                return self._reject(device, cmd_id, reason)
            if device in self.timed:
                self._timed(device, cmd, cmd_id)
            elif device in POSITION_DEVICES:
                pos = int(cmd["position"])
                self.positions[device] = pos
                self.publish(device, {"device": device, "state": "open" if pos else "closed", "event": "moved",
                                      "position": pos, "cmd_id": cmd_id, "ts": self.now()})
            elif device == "doser":
                if not self.timed["pump"].running:  # hardware interlock: doser is wired to the pump relay
                    return self._reject(device, cmd_id, "interlock: pump is not running")
                self.publish("doser", {"device": "doser", "state": "idle", "event": "dose_complete",
                                       "dosed_ml": int(cmd["volume_ml"]), "cmd_id": cmd_id, "ts": self.now()})

    def _reject(self, device: str, cmd_id, reason: str) -> None:
        log.warning("REJECTED %s command %s: %s", device, cmd_id, reason)
        self.publish(device, {"device": device, "state": "unchanged", "event": "rejected", "reason": reason, "cmd_id": cmd_id, "ts": self.now()})

    # ------------------------------------------------- timed devices (pump, heater)
    def _timed(self, device: str, cmd: dict, cmd_id) -> None:
        run = self.timed[device]
        if cmd["action"] == "stop":
            return self._finish(device, "stopped")
        if run.running:
            if device not in RETRIGGERABLE:
                return self._reject(device, cmd_id, f"{device} is already running")
            # Heater and humidifier are re-commanded every sampling interval, and a full-power run lasts exactly one
            # interval, so the next command can arrive just before the last run ends. Close the old run (reporting
            # what it actually delivered) and start the new one. The irrigation pump is NOT re-triggerable.
            self._finish(device, "stopped")
        run.sim_s = int(cmd["duration_s"])
        run.cmd_id = cmd_id
        run.started = self.now()
        real_s = max(run.sim_s / self.sim_speed, MIN_REAL_RUN_S)
        run.timer = self.timer_cls(real_s, self._finish, args=(device, "run_complete"))
        run.timer.daemon = True
        run.timer.start()
        self.publish(device, {"device": device, "state": "running", "event": "run_started", "duration_s": run.sim_s,
                              "cmd_id": cmd_id, "ts": self.now()})

    def _finish(self, device: str, event: str) -> None:
        with self.lock:
            run = self.timed[device]
            if not run.running:
                return
            timer, run.timer = run.timer, None
            timer.cancel()
            elapsed_real = self.now() - run.started
            ran = run.sim_s if event == "run_complete" else min(run.sim_s, round(elapsed_real * self.sim_speed))
            self.publish(device, {"device": device, "state": "idle", "event": event, "ran_s": ran, "cmd_id": run.cmd_id, "ts": self.now()})

    # --------------------------------------------------------------- fail-safe
    def failsafe(self, reason: str) -> None:
        """Connection lost: stop anything that is running (pump, heater, humidifier). The vent is left where it is (a stuck
        vent is safer than one that slams shut)."""
        log.warning("FAILSAFE (%s): stopping timed devices", reason)
        for device in self.timed:
            self._finish(device, "stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    zone = os.environ.get("ZONE", "zone1")
    client = gmqtt.build_client("greengrow-actuators", topics.actuators_status(zone), {"online": False})

    def publish(device, payload):
        gmqtt.publish_json(client, topics.state(zone, device), payload, qos=1, retain=True)

    node = ActuatorNode(publish, sim_speed=float(os.environ.get("SIM_SPEED", "1")))

    def on_connect(c, userdata, flags, reason_code, properties=None):
        log.info("connected to broker (%s)", reason_code)
        gmqtt.publish_json(c, topics.actuators_status(zone), {"online": True}, qos=1, retain=True)
        c.subscribe(topics.CMD_ALL, qos=1)
        for device, state in (("pump", "idle"), ("vent", "closed"), ("doser", "idle"), ("heater", "idle"), ("humidifier", "idle"),
                              ("lamp", "idle"), ("shade", "closed")):  # safe defaults
            publish(device, {"device": device, "state": state, "event": None, "ts": time.time()})

    def on_message(c, userdata, msg):
        data = gmqtt.parse_json(msg.payload)
        device = topics.device_from_topic(msg.topic)
        if data is None:
            log.warning("unparseable command on %s", msg.topic)
            return
        node.handle_command(device, data)

    def on_disconnect(c, userdata, *args):
        node.failsafe("broker disconnected")

    gmqtt.start(client, on_connect=on_connect, on_message=on_message, on_disconnect=on_disconnect)
    log.info("actuator node started")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
