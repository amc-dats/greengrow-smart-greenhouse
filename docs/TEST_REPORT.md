# GreenGrow Orchid: test report

Run on 11 November 2026 against commit `f10210d` (branch `feature/assist-suggestions`, which includes everything on `main` at v1.0 plus the suggestions work). Written from a fresh run of every suite; full output was captured to `snapshots/report/` (not tracked by git).

## Summary

| Level | Result | What it checks |
|---|---|---|
| Backend automated tests | **146 passed**, 0 failed | Safety rules, greenhouse model, sunrise/sunset, suggestions, accounts and roles, API access control, data flow with a stand-in broker, real-weather target runs |
| Actuator node tests | **15 passed** | The node's own hard limits and fail-safe |
| Edge node tests | **4 passed** | Payload building and speed scaling |
| Live system test | **24 of 24 checks passed** | The running stack: devices, data flow, broker security, command path through the real broker, access control, console data |
| Dashboard panel check | **15 of 15 panels return data** | Every Grafana query over the full simulated year (run earlier; not repeated in this run) |
| Orchid targets T1-T7 (closed loop, real weather) | Winter **7 of 7**; summer **6 of 7** | Summer daily light is in band on 79% of days against an 80% bar (documented shortfall) |

The backend suite took 17 min 44 s in this run because the machine was heavily loaded; on a quiet machine it takes about 20 seconds.

## Backend tests by file

| File | Passed | Covers |
|---|---|---|
| `test_rules.py` | 38 | Pump, doser, vent, heater, humidifier, lamp, shade rules; heater and vent setpoints; soak watering; schedule validation |
| `test_flow.py` | 31 | API basics, bridge and controller flow, refusals logged, assist with mock and fake Claude, simulator endpoints |
| `test_accounts.py` | 32 | Registration, sign-in, roles, admin Users page (reset password, change role), lock-out and rate limit, every write endpoint refused for anonymous users and viewers, dashboard data |
| `test_weather_and_realism.py` | 16 | Real-weather closed-loop runs scored on T1-T7, future (climatology) weather |
| `test_environment.py` | 11 | Greenhouse physics model |
| `test_insights.py` | 11 | Suggestions for the period on screen |
| `test_solar.py` | 7 | Sunrise and sunset, day length, photoperiod, winter-rest day band |
| **Total** | **146** | |

## Live system test (`scripts/system_test.py`)

Run with `docker compose exec -T api python - < scripts/system_test.py`. It uses the real broker, bridge, edge node and actuator node.

```
== 1. services and data flow
PASS  API health endpoint answers without a login
PASS  edge node and actuator node are online (Last Will status)   [{'edge-node': True, 'actuators': True}]
PASS  telemetry flows: edge node -> broker -> bridge -> database (a new sample arrived)   [sample 18401 -> 18402 in 4s]
PASS  sample is plausible and carries the outdoor sensor
== 2. broker security
PASS  broker refuses an anonymous connection   [result code 135]
PASS  broker refuses a wrong password   [result code 135]
PASS  broker accepts the backend login   [result code 0]
PASS  ACL: the backend login cannot forge sensor telemetry (the message was not stored)
== 3. command path: API -> safety rules -> bridge -> broker -> actuator node -> state back
PASS  manual command is queued (HTTP 202)   [queued]
PASS  the rules engine decided on it (safe-direction move accepted)   [accepted []]
PASS  the command was published on the real broker (seen by a separate subscriber)   [1 message(s) on the shade command topic]
PASS  the actuator node answered with a state message   [2 state message(s)]
PASS  the API reports the shade at the safe position
PASS  an unsafe command is refused with a reason (nutrients only while the pump runs)   [['nutrient dosing is only allowed while the irrigation pump is running']]
PASS  a refused command never reaches the broker
== 4. access control on the live API
PASS  no login: reading data is refused
PASS  no login: a command is refused
PASS  wrong API key is refused
PASS  the generator endpoint needs its own token
PASS  the generator token alone cannot read data
PASS  fault injection is not available without the admin key
== 5. console data
PASS  dashboard data answers with series, limits and suggestions   [8 suggestion(s)]
PASS  the chart series are complete and aligned   [647 points]
PASS  simulated time moves forward only

24 of 24 checks passed
```

It leaves two manual commands in the command log (a shade move to the safe position and a refused nutrient dose). It does not inject sensor faults, so that no implausible reading is left in the stored history.

## Not tested

- **Browser tests (Playwright).** Written in `e2e/test_auth_ui.py` but never run: the image download was too heavy for the laptop and was cancelled.
- **Real Claude assistant.** Only the mock and a fake client have been exercised; no API key is set.
- **Console in a browser.** The charts, suggestions box and Users page have been checked by their data and HTML only, not by looking at them.
- **Live fault injection, live lock-out and fail-safe on purpose.** Covered by unit tests; not run against the live stack. The actuator fail-safe was seen once in a real log after a broker disconnect.
- **Load or long-run behaviour, TLS, security testing.** Not done. The public tunnel was only checked with curl.

## Open findings

- **Cold late-November in the growing season.** In the generated future weather the live system proposed misting that the rules refused ("too cold to mist, below the 15 C safety minimum") and the greenhouse fell below 15 C by day. Target T1 is proven only for the two test fortnights and the real year; this case is not yet investigated.
- **Assist schedule check and soak watering.** `validate_schedule` does not know about the 2-run soak, so a proposed schedule with two runs 30 minutes apart would be refused although the controller does the same.
- **API container flagged "unhealthy" under load.** Its health check times out after 5 seconds on an overloaded machine while the app still answers requests.
