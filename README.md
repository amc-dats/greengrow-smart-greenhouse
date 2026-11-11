# GreenGrow Smart Greenhouse (integration demonstrator)

Four components, one `docker compose up`:

| Component | What it is | Where |
|---|---|---|
| **Sense** | Simulated Arduino edge node reading soil moisture, temperature, humidity, light; Django is the data generator, driven by real hourly weather for Cranfield, UK (Open-Meteo, cached) | `edge/`, `backend/greenhouse/environment.py` |
| **Act** | Irrigation pump, vent, nutrient doser (manual), heater, humidifier (fine-mist fogger), grow light and shade, with their own hard limits | `actuators/` |
| **Connect** | Mosquitto MQTT broker (authenticated, per-role ACLs) and a bridge into Postgres; Grafana dashboard | `mosquitto/`, `backend/.../bridge.py`, `grafana/` |
| **Assist** | Claude explains readings and suggests watering schedules; a safety rules engine checks them; a human approves | `backend/greenhouse/assist.py`, `rules.py` |

```
 edge-node (Arduino sim) --REST--> django api (generator, Swagger)
        |  MQTT telemetry (QoS1)
        v
   mosquitto  <-----------------------------+
        |                                    |
        v                                    | MQTT state / events
   bridge (the ONLY publisher of commands) --+--> actuators (pump, vent, doser; hard limits, fail-safe)
        |  safety rules check every command
        v
   postgres --> grafana (read-only role)            assist (Claude) -> rules check -> human approval
```

## Start

```bash
cp .env.example .env        # then fill in the secrets (a ready-made .env already exists on the author's laptop)
docker compose up -d --build
```

| | URL |
|---|---|
| Swagger UI (OpenAPI) | http://localhost:8000/api/docs/ |
| Grafana dashboard | http://localhost:3001 (user `admin`, password = `GRAFANA_ADMIN_PASSWORD` in `.env`). The time axis is **simulated time** (UTC), not wall-clock: one simulated day is one day on the axis even at demo speed, and the charts fill in from the left across the 14-day weather period. `greengrow-winter` and `greengrow-summer` are provisioned; the one matching `WEATHER_SCENARIO` opens first. |
| MQTT broker | localhost:1883 (users `edge`, `actuators`, `backend`; passwords in `.env`) |

`WEATHER_SCENARIO` in `.env`: `winter` (default, the coldest fortnight of 2025/26 at Cranfield, freezing outside, the stress test for the orchids) or `summer` (June 2026). Choose it before the first start; to switch afterwards run `docker compose down -v` first.

`SIM_SPEED` in `.env`: `1` = real time (one sample per **30 minutes**, the design value); `180` = demo
(one sample per 10 s; 30 simulated minutes pass in 10 s). `docker compose up -d` after changing it.

The assist runs in **mock** mode until `ANTHROPIC_API_KEY` is set; then it uses Claude (`ASSIST_MODEL`, default
`claude-sonnet-5-5`). 
**Accounts.** Open http://127.0.0.1:8000/ : register (new accounts are **viewers**, read only) or sign in. A **grower** (can water, feed, approve suggestions) or **admin** (also fault injection) is set by an administrator:
`docker compose exec api python manage.py set_role <username> grower|admin|viewer [--create --password ...]`. Every API call except `/health/` needs a sign-in; scripts and curl may use the header `X-API-Key: <API_KEY from .env>` instead (acts as admin; Swagger "Authorize" button). Browser tests (Playwright): see `e2e/test_auth_ui.py`.

## Try it (demo script)

```bash
KEY=<API_KEY from .env>; B=http://localhost:8000/api/v1
curl $B/readings/latest/                                   # sense: latest sample
curl -X POST $B/actuators/pump/command/ -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"action":"run","params":{"duration_s":60}}'      # act: queued, checked, published over MQTT
curl $B/commands/                                          # every command with its safety decision
curl -X POST $B/actuators/pump/command/ -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"action":"run","params":{"duration_s":500}}'      # refused: over the 120 s limit
curl -X POST $B/sim/fault/ -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
     -d '{"metric":"temperature","value":999}'              # inject a faulty sensor; actuation is inhibited
curl -X DELETE $B/sim/fault/ -H "X-API-Key: $KEY"
curl $B/assist/explain/                                    # assist: explanation
curl -X POST $B/assist/suggestions/ -H "X-API-Key: $KEY"   # assist: schedule -> safety check -> pending review
curl -X POST $B/assist/suggestions/1/approve/ -H "X-API-Key: $KEY"   # human approval
```

Watch the MQTT traffic: `docker compose exec mosquitto mosquitto_sub -u backend -P "$MQTT_BACKEND_PASSWORD" -t 'greengrow/#' -v`

## Tests

```bash
docker compose run --rm --no-deps api python -m pytest -q            # 110 backend tests (rules, simulator, weather, orchid targets, API, bridge, assist)
docker compose run --rm --no-deps edge-node python -m pytest -q tests     # 3
docker compose run --rm --no-deps actuators python -m pytest -q tests      # 15
```

Other checks (see script headers for the exact commands): `scripts/validate_dashboard.py` runs every Grafana
panel query through Grafana's API; `scripts/sampling_experiment.py` produces the evidence for the sampling interval.

## Layout

`docker-compose.yml` · `mosquitto/` (broker, ACL) · `postgres/init/` (read-only Grafana role) · `common/greengrow_common/`
(topics, safety limits, MQTT helpers shared by all services) · `backend/` (Django: generator, API, bridge, rules, assist) ·
`edge/` · `actuators/` · `grafana/` (provisioned datasource + dashboard) · `docs/` (decision log, iteration history, AsyncAPI, architecture diagram) · `scripts/`

## Known limitations (be honest about these in the report)

- **Simulated, not physical.** The environment model is simple and not calibrated to a crop; the safety limits are placeholders, not from a standard.
- **No TLS** on MQTT or HTTP (local demo network only). Broker auth and ACLs are on; production would add TLS and per-device certificates.
- **Reads on the REST API are open** on the demo network; writes need an API key.
- **Fault injection** uses a plain API endpoint: remove it in any real deployment.
- The edge node reads its sensors over REST from the generator. On real hardware this is a sensor read; nothing on the MQTT side changes.
- Assist output quality with Claude has not been evaluated here; only its safety wrapper is tested (with a fake client).
- **Night-time humidity in winter is low** (25-41 %): the humidifier is daytime only (wet leaves overnight invite rot). By day it holds 57-60 %.
- **Winter light is low** (outside solar radiation 100-240 W/m2) and there is no grow-light actuator.
- Real weather (Open-Meteo, CC BY 4.0) is downloaded once and cached; it repeats after 14 simulated days.

## Orchid targets and rules

- [docs/ORCHID_TARGETS.md](docs/ORCHID_TARGETS.md): what "healthy and thriving" means as seven measurable targets (Cattleya and Dendrobium), with sources and results.
- [docs/SAFETY_RULES.md](docs/SAFETY_RULES.md): every rule, interlock and control behaviour, with values, where each is enforced, and the tests.
- `scripts/orchid_report.py winter|summer` scores the controller against the targets on the real weather; feeding is manual: `POST /api/v1/feed/`.
