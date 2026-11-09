"""Simulated Arduino edge node (SENSE).

Every sampling interval it reads soil moisture, temperature, humidity and light and publishes ONE
telemetry message over MQTT. The "sensor hardware" is the Django generator, read over REST; on a real
Arduino that call would be replaced by analogRead()/DHT reads and nothing on the MQTT side would change.

Sampling interval: SAMPLE_INTERVAL_MIN simulated minutes (design value 30). SIM_SPEED compresses
simulated time for demos: the real wait between samples is SAMPLE_INTERVAL_MIN * 60 / SIM_SPEED seconds.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

from greengrow_common import mqtt as gmqtt
from greengrow_common import topics

log = logging.getLogger("greengrow.edge")

UNITS = {"soil_moisture": "%", "temperature": "C", "humidity": "%RH", "light": "lux"}
SENSORS = tuple(UNITS)


def real_interval_s(sample_interval_min: float, sim_speed: float) -> float:
    if sample_interval_min <= 0 or sim_speed <= 0:
        raise ValueError("sample interval and sim speed must be positive")
    return sample_interval_min * 60 / sim_speed


def build_payload(sample: dict) -> dict:
    """Telemetry message: one atomic snapshot of all four sensors."""
    missing = [k for k in ("sample_id", "sim_ts", *SENSORS) if k not in sample]
    if missing:
        raise ValueError(f"generator response missing {missing}")
    return {"sample_id": sample["sample_id"], "sim_ts": sample["sim_ts"], **{k: sample[k] for k in SENSORS}, "units": UNITS}


class SensorSource:
    """The 'hardware' read: asks the Django generator to advance the greenhouse and return a sample."""

    def __init__(self, base_url: str, token: str, minutes: float):
        self.url = base_url.rstrip("/") + "/api/v1/sim/step/"
        self.token = token
        self.minutes = minutes

    def read(self) -> dict:
        req = urllib.request.Request(
            self.url,
            data=json.dumps({"minutes": self.minutes}).encode(),
            headers={"Content-Type": "application/json", "X-Sim-Token": self.token},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    zone = os.environ.get("ZONE", "zone1")
    minutes = float(os.environ.get("SAMPLE_INTERVAL_MIN", "30"))
    wait_s = real_interval_s(minutes, float(os.environ.get("SIM_SPEED", "1")))
    source = SensorSource(os.environ.get("GENERATOR_URL", "http://api:8000"), os.environ["SIM_TOKEN"], minutes)

    client = gmqtt.build_client("greengrow-edge", topics.edge_status(zone), {"online": False})

    def on_connect(c, userdata, flags, reason_code, properties=None):
        log.info("connected to broker (%s)", reason_code)
        gmqtt.publish_json(c, topics.edge_status(zone), {"online": True}, qos=1, retain=True)

    gmqtt.start(client, on_connect=on_connect)
    log.info("edge node started: a sample every %.1f s real time (%g simulated minutes)", wait_s, minutes)

    while True:
        started = time.monotonic()
        try:
            payload = build_payload(source.read())
            if gmqtt.publish_json(client, topics.telemetry(zone), payload, qos=1):
                log.info("published sample %s %s", payload["sample_id"], {k: payload[k] for k in SENSORS})
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            # A failed read is skipped, not invented: no data is better than made-up data.
            log.warning("sensor read failed (%s); skipping this sample", exc)
        time.sleep(max(0.0, wait_s - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
