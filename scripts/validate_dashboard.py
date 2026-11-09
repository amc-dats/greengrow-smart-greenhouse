"""Runs every panel query in the Grafana dashboard through Grafana's own datasource API and reports
status and row counts, so a broken query cannot hide behind a blank panel.

Run from the project root (needs GRAFANA_ADMIN_PASSWORD; Grafana on localhost:3001):
  docker run --rm --add-host=host.docker.internal:host-gateway -v "$PWD/grafana/dashboards:/d:ro" \
      -e GRAFANA_URL=http://host.docker.internal:3001 -e GRAFANA_ADMIN_PASSWORD=... \
      python:3.12-slim python - < scripts/validate_dashboard.py
"""
import base64
import json
import os
import urllib.request

url = os.environ.get("GRAFANA_URL", "http://localhost:3001")
auth = base64.b64encode(f"admin:{os.environ['GRAFANA_ADMIN_PASSWORD']}".encode()).decode()
dash = json.load(open(os.environ.get("DASHBOARD", "/d/greengrow.json")))

bad = 0
for p in dash["panels"]:
    body = {"queries": [{**t, "datasource": p["datasource"]} for t in p["targets"]], "from": "now-24h", "to": "now"}
    for q in body["queries"]:
        q["rawSql"] = q["rawSql"].replace("$__timeFilter(ts)", "ts > now() - interval '30 days'")
    req = urllib.request.Request(url + "/api/ds/query", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Basic " + auth})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=20))["results"]["A"]
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
