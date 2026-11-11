"""MQTT topic scheme shared by every service. See docs/asyncapi.yaml.

greengrow/{zone}/sensors/telemetry          edge  -> backend   one JSON sample with all four sensors
greengrow/{zone}/edge/status                edge  -> backend   retained online/offline (Last Will)
greengrow/{zone}/actuators/{device}/cmd     backend -> actuators  command (QoS 1)
greengrow/{zone}/actuators/{device}/state   actuators -> backend  retained state + events
greengrow/{zone}/actuators/status           actuators -> backend  retained online/offline (Last Will)
"""

BASE = "greengrow"
DEVICES = ("pump", "vent", "doser", "heater", "humidifier", "lamp", "shade")


def telemetry(zone: str) -> str:
    return f"{BASE}/{zone}/sensors/telemetry"


def edge_status(zone: str) -> str:
    return f"{BASE}/{zone}/edge/status"


def cmd(zone: str, device: str) -> str:
    return f"{BASE}/{zone}/actuators/{device}/cmd"


def state(zone: str, device: str) -> str:
    return f"{BASE}/{zone}/actuators/{device}/state"


def actuators_status(zone: str) -> str:
    return f"{BASE}/{zone}/actuators/status"


# Wildcard subscriptions
CMD_ALL = f"{BASE}/+/actuators/+/cmd"
STATE_ALL = f"{BASE}/+/actuators/+/state"
TELEMETRY_ALL = f"{BASE}/+/sensors/telemetry"
EDGE_STATUS_ALL = f"{BASE}/+/edge/status"
ACTUATORS_STATUS_ALL = f"{BASE}/+/actuators/status"


def device_from_topic(topic: str) -> str:
    """greengrow/zone1/actuators/pump/cmd -> 'pump'."""
    parts = topic.split("/")
    return parts[3] if len(parts) >= 5 else ""
