"""Runs every panel query of a Grafana dashboard through Grafana's own datasource API, over the dashboard's own time
range, and reports status and row counts, so a broken query cannot hide behind a blank panel.

Run from the project root (needs GRAFANA_ADMIN_PASSWORD; Grafana on localhost:3001):
  docker run --rm -i --add-host=host.docker.internal:host-gateway -v "$PWD:/w:ro" \
      -e GRAFANA_URL=http://host.docker.internal:3001 -e GRAFANA_ADMIN_PASSWORD=... \
      -e DASHBOARD=/w/grafana/dashboards/greengrow-winter.json python:3.12-slim python - < scripts/validate_dashboard.py
"""
import base64
import json
import os
from datetime import datetime
import urllib.request

url = os.environ.get("GRAFANA_URL", "http://localhost:3001")
auth = base64.b64encode(f"admin:{os.environ['GRAFANA_ADMIN_PASSWORD']}".encode()).decode()
dash = json.load(open(os.environ.get("DASHBOARD", "/w/grafana/dashboards/greengrow-winter.json")))


def epoch_ms(iso: str) -> str:
    return str(int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000))


t = dash.get("time", {"from": "now-24h", "to": "now"})
rng = {"from": epoch_ms(t["from"]) if t["from"][:1].isdigit() else t["from"], "to": epoch_ms(t["to"]) if t["to"][:1].isdigit() else t["to"]}
print(f"dashboard '{dash['title']}', time range {t['from']} -> {t['to']}")

bad = 0
for p in dash["panels"]:
    body = {"queries": [{**q, "datasource": p["datasource"]} for q in p["targets"]], **rng}
    req = urllib.request.Request(url + "/api/ds/query", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Basic " + auth})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=30))["results"]["A"]
        frames = res.get("frames", [])
        rows = sum(len(f["data"]["values"][0]) if f["data"]["values"] else 0 for f in frames)
        status = "OK " if res.get("status") == 200 and not res.get("error") else "ERR"
        detail = f"{rows} rows"
    except Exception as exc:  # noqa: BLE001
        status, detail = "ERR", str(exc)[:120]
    bad += status == "ERR"
    print(f"{status} panel {p['id']:>2} {p['title'][:52]:<52} {detail}")
print("all panel queries OK" if not bad else f"{bad} panel(s) failing")
raise SystemExit(1 if bad else 0)
