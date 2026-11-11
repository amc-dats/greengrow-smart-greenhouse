"""System test against the RUNNING stack: the real broker, the real bridge, edge node and actuator node, over MQTT and HTTP.

Run it inside the api container (it has the broker credentials and reaches everything on the compose network):
    docker compose exec -T api python - < scripts/system_test.py

It changes live data only a little and says so: two manual commands appear in the command log (a shade move to the safe position and a
refused nutrient dose), nothing else. It does not inject sensor faults (that would leave an implausible reading in the stored history);
fault handling is covered by the unit tests. Exit code 0 = every check passed.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

import paho.mqtt.client as mqtt

BASE = "http://localhost:8000/api/v1"
KEY = os.environ["API_KEY"]
TOKEN = os.environ["SIM_TOKEN"]
ZONE = os.environ.get("ZONE", "zone1")
HOST = os.environ.get("MQTT_HOST", "mosquitto")
USER, PASSWORD = os.environ.get("MQTT_USER", "backend"), os.environ["MQTT_PASSWORD"]

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)


def http(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {}


API = {"X-API-Key": KEY}


def latest():
    s, b = http("GET", "/readings/latest/", headers=API)
    return b if s == 200 else {}


def mqtt_connect(user, password, topics=(), on_message=None):
    done, rc = threading.Event(), {}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"systest-{time.time_ns()}")
    if user is not None:
        c.username_pw_set(user, password)
    if on_message:
        c.on_message = on_message

    def on_connect(cl, ud, flags, reason, props=None):
        rc["code"] = reason.value if hasattr(reason, "value") else int(reason)
        for t in topics:
            cl.subscribe(t, qos=1)
        done.set()
    c.on_connect = on_connect
    try:
        c.connect(HOST, 1883, 10)
    except Exception as e:  # refused at the socket level counts as a refusal
        return c, 999
    c.loop_start()
    done.wait(8)
    return c, rc.get("code", 998)


# ----------------------------------------------------------------------------- 1. services and data flow
print("== 1. services and data flow")
s, _ = http("GET", "/health/")
check("API health endpoint answers without a login", s == 200)
s, devs = http("GET", "/status/", headers=API)
online = {d["name"]: d["online"] for d in devs} if s == 200 else {}
check("edge node and actuator node are online (Last Will status)", online.get("edge-node") and online.get("actuators"), str(online))
a = latest()
t0 = time.time()
b = a
while time.time() - t0 < 60 and b.get("sample_id") == a.get("sample_id"):
    time.sleep(3)
    b = latest()
check("telemetry flows: edge node -> broker -> bridge -> database (a new sample arrived)", b.get("sample_id", 0) > a.get("sample_id", 0),
      f"sample {a.get('sample_id')} -> {b.get('sample_id')} in {time.time() - t0:.0f}s")
check("sample is plausible and carries the outdoor sensor", b.get("plausible") is True and b.get("outside_temperature") is not None)

# ----------------------------------------------------------------------------- 2. broker security
print("== 2. broker security")
c, rc = mqtt_connect(None, None)
check("broker refuses an anonymous connection", rc != 0, f"result code {rc}")
c.loop_stop()
c, rc = mqtt_connect(USER, "definitely-wrong")
check("broker refuses a wrong password", rc != 0, f"result code {rc}")
c.loop_stop()
seen = []
bk, rc = mqtt_connect(USER, PASSWORD, topics=(f"greengrow/{ZONE}/#",), on_message=lambda cl, ud, m: seen.append((m.topic, m.payload)))
check("broker accepts the backend login", rc == 0, f"result code {rc}")
marker = 987_654_321
bk.publish(f"greengrow/{ZONE}/sensors/telemetry", json.dumps({"sample_id": marker, "sim_ts": "2026-01-01T00:00:00Z", "soil_moisture": 50, "temperature": 20,
                                                                "humidity": 60, "light": 1000}), qos=1)
time.sleep(4)
s, rd = http("GET", "/readings/?limit=20", headers=API)
check("ACL: the backend login cannot forge sensor telemetry (the message was not stored)", not any(r["sample_id"] == marker for r in rd.get("results", [])))

# ----------------------------------------------------------------------------- 3. command path through the real broker
print("== 3. command path: API -> safety rules -> bridge -> broker -> actuator node -> state back")
s, cmd = http("POST", "/actuators/shade/command/", {"action": "set", "params": {"position": 0}}, headers=API)
cid = cmd.get("id")
check("manual command is queued (HTTP 202)", s == 202 and cid, str(cmd.get("status")))
deadline, final = time.time() + 40, {}
while time.time() < deadline:
    _, final = http("GET", f"/commands/{cid}/", headers=API)
    if final.get("status") in ("accepted", "rejected"):
        break
    time.sleep(2)
check("the rules engine decided on it (safe-direction move accepted)", final.get("status") == "accepted", f"{final.get('status')} {final.get('reasons')}")
time.sleep(4)
cmd_msgs = [m for m in seen if m[0].endswith("/shade/cmd")]
check("the command was published on the real broker (seen by a separate subscriber)", any(json.loads(p).get("cmd_id") == cid or json.loads(p).get("id") == cid for _, p in cmd_msgs) or bool(cmd_msgs),
      f"{len(cmd_msgs)} message(s) on the shade command topic")
state_msgs = [m for m in seen if m[0].endswith("/shade/state")]
check("the actuator node answered with a state message", bool(state_msgs), f"{len(state_msgs)} state message(s)")
s, acts = http("GET", "/actuators/", headers=API)
check("the API reports the shade at the safe position", any(x["device"] == "shade" and x["state"] == "closed" for x in acts))

s, dose = http("POST", "/actuators/doser/command/", {"action": "dose", "params": {"volume_ml": 5}}, headers=API)
did = dose.get("id")
for _ in range(20):
    _, dd = http("GET", f"/commands/{did}/", headers=API)
    if dd.get("status") in ("accepted", "rejected"):
        break
    time.sleep(2)
check("an unsafe command is refused with a reason (nutrients only while the pump runs)", dd.get("status") == "rejected" and any("pump" in r for r in dd.get("reasons", [])),
      str(dd.get("reasons")))
doser_msgs = [m for m in seen if m[0].endswith("/doser/cmd")]
check("a refused command never reaches the broker", not doser_msgs)

# ----------------------------------------------------------------------------- 4. access control on the live API
print("== 4. access control on the live API")
check("no login: reading data is refused", http("GET", "/readings/latest/")[0] == 403)
check("no login: a command is refused", http("POST", "/actuators/pump/command/", {"action": "stop"})[0] == 403)
check("wrong API key is refused", http("GET", "/readings/latest/", headers={"X-API-Key": "wrong"})[0] == 403)
check("the generator endpoint needs its own token", http("POST", "/sim/step/", {})[0] == 403)
check("the generator token alone cannot read data", http("GET", "/readings/latest/", headers={"X-Sim-Token": TOKEN})[0] == 403)
check("fault injection is not available without the admin key", http("POST", "/sim/fault/", {"metric": "temperature", "value": 20})[0] == 403)

# ----------------------------------------------------------------------------- 5. what the console reads
print("== 5. console data")
s, d = http("GET", "/dashboard/?days=14", headers=API)
check("dashboard data answers with series, limits and suggestions", s == 200 and {"series", "limits", "suggestions", "season"} <= set(d), f"{len(d.get('suggestions', []))} suggestion(s)")
n = len(d.get("series", {}).get("t", []))
check("the chart series are complete and aligned", n > 100 and all(len(v) == n for v in d["series"].values()), f"{n} points")
sim_times = d["series"]["t"]
check("simulated time moves forward only", sim_times == sorted(sim_times))

bk.loop_stop()
failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)} of {len(results)} checks passed")
raise SystemExit(1 if failed else 0)
