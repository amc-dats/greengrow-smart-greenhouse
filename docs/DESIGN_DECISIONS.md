# Design decision log

Each row: the choice, the alternatives, the justification, and the evidence in this repo.
"You" = decided by the group; "Default" = a default Claude Code picked while building, to be confirmed or changed.

## Crop and targets: delicate tropical (warm-growing) orchids

The control targets come from orchid-society guidance. These are guideline figures, **not a standard**, and the sources
disagree at the margins: [AOS, Cold Tolerance of Warm Growing Orchids](https://www.aos.org/orchids/articles/cold-tolerance-of-warm-growing-orchids);
NYBG/AOS temperature table (warm class: 24-29 C by day, minimum about 18 C at night); Iowa State Extension and RNZIH moth-orchid guides
(avoid below about 12-13 C, humidity roughly 50-80 %). Set in `common/greengrow_common/safety_limits.py`.

| Quantity | Value | Where it comes from |
|---|---|---|
| **Safety minimum temperature** | **15 C** | Set by the grower. The vent may not open below it; the dashboard turns red. |
| Recommended night minimum | 18 C | Warm-class guideline (about 65 F). |
| Daytime band | 24-28 C | Warm-class guideline; the vent holds it. |
| Heater setpoint / full-power point | 20 C / 16 C | Default: proportional control, a margin of 1 C above the safety minimum at full power. |
| Heater hard limit | no heating at or above 22 C | Default. |
| Humidity band | 50-80 % | Guideline range, shown on the dashboard; the humidifier works to it. |
| Humidifier setpoint / full-power point | 65 % / 50 % | Default: proportional control; refused at 80 % or above. |
| Misting hours | 07:00-17:00 (simulated clock) | Default: daytime only so leaves dry before night (wet leaves overnight invite rot and fungus). |

## Integration decisions

| # | Decision | Alternatives | Justification | Evidence | Who |
|---|---|---|---|---|---|
| 1 | **MQTT** for device-to-device messaging | HTTP polling, CoAP, AMQP, Kafka | Publish/subscribe decouples sensors, actuators and consumers (any can restart independently); tiny overhead for constrained devices; QoS levels; retained state; Last Will for offline detection. | `mosquitto/`, status panel | You |
| 2 | **REST + OpenAPI (Swagger)** for human/client interfaces | GraphQL, gRPC | Request/response fits queries, manual commands and approvals; OpenAPI is a standard, self-documenting and generates a UI. MQTT is described separately with **AsyncAPI** because OpenAPI cannot express pub/sub. | `/api/docs/`, `docs/asyncapi.yaml` | You (AsyncAPI pairing: Default) |
| 3 | **Django as the data generator** | Plain Python script, Node-RED | One framework serves the generator, REST API, persistence and Swagger. The generator is a causal model (outside weather, actuators and plants all change the readings) so the closed loop is testable. | `environment.py`, `test_environment.py` | You |
| 4 | **Sampling every 30 simulated minutes** | 30 s, 5 min, 1 h | See the evidence section below. | `scripts/sampling_experiment.py` | You |
| 5 | **Single telemetry message** (all four sensors in one JSON) | One topic per metric | One transmission per sample (less radio/power on an Arduino) and an atomic snapshot taken at one moment. | `edge_node.build_payload` | **Default** |
| 6 | **Bridge is the only publisher of commands**; REST only queues | API publishes directly | A single choke point: every command, from any source, passes the same safety check and is logged. | `controller.submit` | Default |
| 7 | **Postgres**, Grafana via a **read-only role** | InfluxDB, TimescaleDB | Django needs a relational store anyway. Least privilege: the dashboard cannot alter data. | `postgres/init/` | You |
| 8 | **Grafana in compose** (host port 3001) | Existing local or cloud Grafana | Reproducible for the group; Grafana Cloud cannot reach a local Postgres without exposing it. | `grafana/provisioning` | You |
| 9 | **Time acceleration** (`SIM_SPEED`) | Pre-seeded data, real time only | Design stays 30 min; demos compress time. Safety timing uses simulated time so rules behave the same at any speed. | `.env` | You |
| 10 | **Broker auth + ACLs, clean sessions** | Anonymous broker | Only the backend may publish commands. A command sent while an actuator is offline is dropped, not delivered late; commands also expire after 60 s. | `mosquitto/acl` | Default |
| 11 | **Real weather input** for a location (Cranfield, UK): outside temperature, humidity and solar radiation | Synthetic sine-wave day | The earlier made-up outside temperature gave a 10-28 C daily swing that tracked the sun and looked like a faulty sensor. Real hourly data gives realistic structure (cold snaps, cloudy days). Downloaded **once** and cached, so the stack runs offline and every run is reproducible. | `scripts/fetch_weather.py`, `backend/greenhouse/data/` | You |
| 12 | **Winter scenario = coldest fortnight of 2025/26** (from 29 Dec 2025); summer scenario = June 2026 | A typical week | The orchids must be protected from winter cold, so the default scenario is the hardest case (min -5.2 C outside, 115 hours below 0 C). `WEATHER_SCENARIO=summer` switches. | cached JSON files | Default (period choice) |
| 13 | **Thermal-mass greenhouse model** | Temperature follows sunlight directly | Heat gain from sun, loss to outside air, vent exchange and a heat-storing mass give a slow morning rise, a held daytime level and a gentle evening decline. | `environment.py`, `test_weather_and_realism.py` | You (shape) |
| 14 | **Heater actuator with proportional control** | Add nothing (vent only); on/off thermostat | Vent-only cannot stop a winter greenhouse falling towards freezing. An on/off thermostat in 30-minute steps cycled the temperature by up to 2.5 C; proportional control (run length grows smoothly below 20 C) holds it steady. | `rules.auto_decide`, `closed_loop_report.py` | You (heater); Default (proportional) |
| 15 | **Heater safety interlocks** | none | Max 30 min per command (must be re-commanded); refused at 22 C or above; refused while the vent is open; the vent may not open while it runs; stops if the link is lost. | `rules.py`, `actuator_node.py` | Default |

| 16 | **Humidifier as a new, fifth actuator** (fine-mist fogger) alongside the irrigation pump | Redefine the pump as a humidity-triggered sprayer; a spray nozzle that wets leaves | Closed-loop runs showed winter humidity averaging about 33 % against the orchids' 50-80 %: heating cold outside air dries it. Substrate watering (soil moisture) and air humidity are different jobs, so they get different devices; the tested irrigation logic and the doser interlock stay unchanged. A fogger adds vapour to the air without wetting leaves or the potting medium. | `rules._check_humidifier`, `environment.advance`, `test_weather_and_realism.py` | You |
| 17 | **Proportional humidity control, daytime only, with lockouts** | On/off threshold; any time of day | Run length grows smoothly as humidity falls below 65 % (full at 50 %), avoiding on/off cycling. Daytime only, so leaves dry before night; refused above 80 % humidity, below the 15 C safety minimum, and while the vent is open (the water would be wasted); the vent may not open while it runs. | `rules.py`, `test_rules.py` | You |
| 18 | **Heater and humidifier can be re-triggered; the irrigation pump cannot** | Reject any command while running | A full-power run lasts exactly one sampling interval, so the next command can arrive just before the last run ends. Rejecting it lost heating for a whole interval (small temperature dips seen in live testing). Now the old run is closed with what it delivered and the new one starts. A pump run must never be extended. | `actuator_node._timed`, `test_actuators.py` | Default (found in live testing) |
| 19 | **The edge node waits until the bridge is subscribed** | Start everything together | The first telemetry messages were being lost at start-up (no subscriber yet, clean sessions), so the first heater commands were never issued. The bridge healthcheck now turns healthy only once it is connected and subscribed. | `run_bridge.py`, `docker-compose.yml` | Default (found in live testing) |

## Safety decisions (placeholders, change in `safety_limits.py`)

| Rule | Value | Notes |
|---|---|---|
| Irrigation | start below 30 %, size to reach 60 %, never projected above 70 %, max 120 s, 4 runs/day, 3 h gap | Manual and scheduled runs are allowed whenever moisture is under 60 %. **Interpretation chosen by Claude Code, please confirm.** |
| Vent | open above 28 C, close below 24 C, humidity above 85 % also opens it when warm, never opens below 15 C, 10 min between moves | |
| Heater | proportional below 20 C (full at 16 C), max 1800 s per command, refused at 22 C and while the vent is open | |
| Humidifier | proportional below 65 % (full at 50 %), daytime only (07:00-17:00), max 1800 s per command, refused at 80 % humidity, below 15 C and while the vent is open | Stops if the link is lost. |
| Nutrient doser | only while the pump runs, 50 mL/dose, 200 mL/day, 12 h gap | Also interlocked in the actuator node. |
| Data gates | stale (>2 intervals), implausible range (-10 to 60 C), missing: all inhibit actuation; stop/close always allowed | Fault injection (temperature faults limited to -100..100 C) tests this. |
| Defence in depth | backend rules (layer 1) + actuator hard limits, command expiry, interlocks, fail-safe (layer 2) | `test_actuators.py` |
| Assist | advisory only; schedule must pass rules, be approved by a human, and each run is re-checked when executed | `assist.py`, `test_flow.py` |

## Evidence: closed-loop behaviour on real weather (`scripts/closed_loop_report.py`, also asserted in tests)

14 simulated days, rules-based controller, actuators respond instantly.

| Scenario | Outside | Greenhouse air | Largest change between samples | Humidity |
|---|---|---|---|---|
| Winter (from 29 Dec 2025) | -5.2 to 9.1 C | **17.1 to 18.6 C** (never near the 15 C minimum) | 0.60 C | **daytime 57-60 %** (in band, was about 33 % without the humidifier); night 25-41 % |
| Summer (from 1 Jun 2026) | 7.8 to 21.3 C | 18.6 to 24.5 C; afternoon peak around 14:00-16:00, gentle evening decline | 0.78 C | daytime 62-72 %, 50-76 % overall |

For comparison, the previous generator (made-up sun-following model) swung 10-28 C a day with 3-4 C per hour rises and no plateau.

## Evidence for the 30-minute sampling interval

Method: 14 simulated days at 1-minute resolution on real June weather (no actuators, no noise) as ground truth; re-sample at several
intervals; measure interpolation error and how long a controller would take to notice a threshold crossing.

| Interval | msgs/day | RMSE temp (C) | RMSE moisture (%) | RMSE light (lux) | Mean delay: temp rising through 20 C (11 events) | Mean delay: moisture below 30 % (1 event) |
|---|---|---|---|---|---|---|
| 1 min | 1440 | 0 | 0 | 0 | 0 min | 0 min |
| 5 min | 288 | 0.003 | 0.001 | 31 | 1.8 min | 0 min |
| 15 min | 96 | 0.004 | 0.001 | 59 | 7.3 min | 10 min |
| **30 min** | **48** | **0.010** | **0.003** | **86** | **10.0 min** | **25 min** |
| 60 min | 24 | 0.036 | 0.016 | 123 | 20.9 min | 55 min |
| 120 min | 12 | 0.089 | 0.041 | 3761 | 48.2 min | 115 min |
| 240 min | 6 | 0.273 | 0.181 | 5970 | 244.5 min | 235 min |

Reading it: at 30 min the reconstruction error is far below sensor noise for temperature (0.01 C vs 0.1 C noise) and moisture (0.003 % vs 0.3 %).
Light is the fast variable: its error is small up to 60 min but jumps at 2 h because the hourly weather data and passing cloud are no longer resolved.
A morning warm-up is noticed 10 min late on average. Going faster buys little (5 min saves about 8 min of delay for 6x the traffic); going slower
(2 h+) starts to miss events. **Caveats for the report:** the ground truth is a simulation driven by hourly weather, not a measured greenhouse;
the moisture result rests on a single crossing (n=1), so it is weak evidence; fast events (a cold draught, a pump fault) need event-driven
alerts, not faster polling.

## Known limitations and findings

- **Night-time humidity in winter is still low** (25-41 %), because misting is daytime only by your choice (wet leaves overnight invite rot). By day the humidifier holds
  57-60 %. If the orchids need more at night, the options are a longer misting window ending earlier in the evening, or a fogger that adds vapour without wetting
  leaves at night (decision for the group).
- The humidifier's water use is not modelled (no tank level or consumption limit).
- **Winter light is low**: outside solar radiation peaks at only 100-240 W/m2, about 8-20 klux inside the greenhouse; orchids in a UK winter would need grow lights.
  Light is a sensor only; there is no light actuator.
- Weather repeats from its start after 14 simulated days (a documented simplification).
- Not evaluated: quality of Claude's explanations and schedules (only the safety wrapper is tested), real-hardware behaviour, load/soak testing, TLS.
- Weather data by [Open-Meteo.com](https://open-meteo.com) (CC BY 4.0).
