"""Thin helpers around paho-mqtt 2.x used by the edge node, the actuators and the backend bridge."""
from __future__ import annotations

import json
import logging
import os

import paho.mqtt.client as mqtt

log = logging.getLogger("greengrow.mqtt")


def build_client(client_id: str, will_topic: str | None = None, will_payload: dict | None = None):
    """Create a client from MQTT_USER / MQTT_PASSWORD env vars, with an optional retained Last Will.

    Sessions are clean (not persistent) on purpose: a command that arrives while an actuator is
    offline must be dropped, never delivered late.
    """
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
    client.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASSWORD"])
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    if will_topic:
        client.will_set(will_topic, json.dumps(will_payload or {"online": False}), qos=1, retain=True)
    return client


def start(client, on_connect=None, on_message=None, on_disconnect=None):
    """Attach callbacks and connect asynchronously; the network loop retries until the broker is up."""
    if on_connect:
        client.on_connect = on_connect
    if on_message:
        client.on_message = on_message
    if on_disconnect:
        client.on_disconnect = on_disconnect
    client.connect_async(os.environ.get("MQTT_HOST", "mosquitto"), int(os.environ.get("MQTT_PORT", "1883")), keepalive=30)
    client.loop_start()


def publish_json(client, topic: str, payload: dict, qos: int = 1, retain: bool = False) -> bool:
    info = client.publish(topic, json.dumps(payload), qos=qos, retain=retain)
    ok = info.rc == mqtt.MQTT_ERR_SUCCESS
    if not ok:
        log.warning("publish to %s failed (rc=%s)", topic, info.rc)
    return ok


def parse_json(raw: bytes) -> dict | None:
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None
