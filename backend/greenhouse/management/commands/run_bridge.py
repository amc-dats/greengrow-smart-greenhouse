"""MQTT bridge: the single choke point between the broker and the database/safety rules."""
import logging
import queue
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from greengrow_common import mqtt as gmqtt
from greengrow_common import topics
from greenhouse import bridge, controller

log = logging.getLogger("greengrow.bridge")
READY_FILE = Path("/tmp/bridge_ready")


class Command(BaseCommand):
    help = "Run the MQTT bridge (telemetry in, safety-checked commands out)."

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        inbox: queue.Queue = queue.Queue()
        client = gmqtt.build_client("greengrow-bridge")

        def on_connect(c, userdata, flags, reason_code, properties=None):
            log.info("connected to broker (%s)", reason_code)
            c.subscribe([(t, 1) for t in (topics.TELEMETRY_ALL, topics.STATE_ALL, topics.EDGE_STATUS_ALL, topics.ACTUATORS_STATUS_ALL)])

        def on_subscribe(c, userdata, mid, reason_codes, properties=None):
            READY_FILE.touch()  # compose healthcheck: the edge node starts only once the bridge is really subscribed

        def on_disconnect(c, userdata, *args):
            READY_FILE.unlink(missing_ok=True)

        def on_message(c, userdata, msg):
            inbox.put((msg.topic, gmqtt.parse_json(msg.payload)))

        def publish(topic, payload):
            return gmqtt.publish_json(client, topic, payload, qos=1)

        client.on_subscribe = on_subscribe
        gmqtt.start(client, on_connect=on_connect, on_message=on_message, on_disconnect=on_disconnect)
        log.info("bridge started (auto_control=%s, zone=%s)", settings.AUTO_CONTROL, settings.ZONE)

        while True:
            try:
                topic, payload = inbox.get(timeout=1.0)
            except queue.Empty:
                topic = payload = None
            try:
                close_old_connections()
                if payload is not None:
                    bridge.handle_message(topic, payload, publish)
                controller.dispatch_queued(publish)  # commands queued through the REST API
            except Exception:  # keep the bridge alive; the next message retries
                log.exception("error while handling %s", topic)
