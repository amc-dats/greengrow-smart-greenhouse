# Safety rules, interlocks and control behaviour

Every rule in the system, with its value, where it is enforced, and the test that checks it. All values live in one file,
`common/greengrow_common/safety_limits.py`, imported by every service. They are **placeholders agreed for the coursework**, not values from a standard
or from a crop specification. The orchid targets they serve are in [ORCHID_TARGETS.md](ORCHID_TARGETS.md).

All times are **simulated time, UTC**. "Day" is **sunrise to sunset**, computed from the date and the site location (Cranfield; about 7.8 h in late December, 16.4 h in June; fixed 07:00-19:00 is only a fallback if no sun times exist). The **winter rest** is December to February; every other month is the **growing season**.

## 1. Design principles

| # | Principle | How |
|---|---|---|
| S1 | **Defence in depth** (two independent layers) | Layer 1: the backend rules engine. Layer 2: the actuator node re-checks hard limits and does not trust the backend. |
| S2 | **One choke point** | The bridge is the only publisher of actuator commands. Automatic, manual and scheduled commands all pass the same check and are all logged with the decision. |
| S3 | **Safe direction is never blocked** | Any `stop`, vent to 0 and shade to 0 are always allowed, even with stale or implausible data. |
| S4 | **Fail safe** | If publishing fails the command is refused, never queued. If the actuator node loses the broker it stops the pump, heater, humidifier and grow light. |
| S5 | **Limits sit outside the AI** | The Claude assistant only advises. Its schedules are checked by the rules, approved by a person, and re-checked when each run executes. |
| S6 | **Least privilege** | Per-role broker permissions (only the backend may publish commands); Grafana database role is read-only; every API call needs a sign-in; roles viewer / grower / admin (changing anything needs grower; fault injection needs admin). |
| S7 | **Stale commands die** | Commands expire after 60 s; clean MQTT sessions mean a command sent to an offline actuator is dropped, not delivered late. |

## 2. Calendar and time windows

| Name | Value | Used for |
|---|---|---|
| Day | sunrise to sunset (`greenhouse/solar.py`) | heater setpoint, vent logic, targets |
| Winter rest | December, January, February | lower safety minimum, almost no water, no fertiliser, no night venting |
| Humidifier hours | sunrise to 2 h before sunset | misting is daytime only, so leaves dry before night |
| Lamp months / hours | November-February / the day extended to a **14-hour photoperiod** centred on solar noon | grow light is a winter top-up only |
| Shade months | May-August | shade is for very strong summer light only (automatic control; not a safety rule) |

## 3. Data gates (before any actuation)

| # | Rule | Value | Layer |
|---|---|---|---|
| D1 | No sensor data yet: all actuation inhibited | | backend |
| D2 | Stale data: inhibited if the latest reading is older than | 2 sampling intervals (+10 s) | backend |
| D3 | Implausible data: inhibited if any reading is outside | soil moisture 0-100 %, temperature -10 to 60 C, humidity 0-100 %, light 0-120,000 lux (or missing / not a number) | backend |
| D4 | Exempt (safe direction): stop commands, vent to 0, shade to 0 | | backend |

Tests: `test_no_data_stale_and_implausible_block_actuation_but_not_stop`, `test_implausible_sensor_value_inhibits_actuation`, `test_stale_data_blocks_manual_command`.

## 4. Rules per device (backend, layer 1)

Command structure is checked first for every device: known device and action, parameters an object with no unexpected keys, and whole-number values inside the limits below.

### Irrigation pump: a dry-down cycle with a seasonal rest
| # | Rule | Growing season | Winter rest |
|---|---|---|---|
| P1 | Run length | 1-120 s | 1-120 s |
| P2 | Refused if soil moisture is at or above the season's target | 60 % | 40 % |
| P3 | Refused if the projected moisture would exceed (0.15 % per second assumed) | 70 % | 70 % |
| P4 | Maximum runs in 24 h | 2 | 2 |
| P5 | Minimum gap since the last watering (the medium must dry down) | 48 h | 168 h |

Tests: `test_pump_refused_when_moisture_at_target...`, `test_pump_dry_down_gap_daily_limit`, `test_winter_rest_waters_much_less`.

### Nutrient doser: manual only, interlocked to the pump
| # | Rule | Value |
|---|---|---|
| N1 | Dose size | 1-50 mL |
| N2 | Refused in the winter rest | no fertiliser December-February |
| N3 | Only while the irrigation pump is running (interlock) | |
| N4 | Maximum per 24 h | 200 mL |
| N5 | Minimum gap between doses | 12 h |

Feeding is **manual** (decision: the grower decides, "weakly, weekly"). The controller never proposes a dose. `POST /api/v1/feed/` queues a watering and a dose together so the interlock sees the pump running; if the watering is refused, the dose is refused too.
Tests: `test_doser_only_while_pump_running`, `test_doser_daily_limit_gap_and_no_fertiliser_in_the_rest`, `test_feeding_is_manual_and_goes_through_the_interlock`, `test_no_feeding_in_the_winter_rest`.

### Heater
| # | Rule | Value |
|---|---|---|
| H1 | Run length per command (it must be re-commanded each interval to continue) | 1-1800 s |
| H2 | Refused at or above | 27 C |
| H3 | Refused while the vent is open more than | 10 % |
| H4 | The vent may not be opened more than 10 % while the heater is running (see V4) | |

### Humidifier (fine-mist fogger: adds vapour to the air, does not wet leaves or the medium)
| # | Rule | Value |
|---|---|---|
| M1 | Run length | 1-1800 s |
| M2 | Refused at or above this humidity | 80 % |
| M3 | Refused below the season's safety minimum temperature | 15 C (10 C in the rest) |
| M4 | Refused while the vent is open more than | 10 % |
| M5 | Daytime only | sunrise to 2 h before sunset |

### Vent motor
| # | Rule | Value |
|---|---|---|
| V1 | Position | 0-100 % |
| V2 | **Cold protection:** may not open (position above 0) below the season's safety minimum | 15 C (10 C in the rest) |
| V3 | Minimum time between vent moves | 10 min |
| V4 | May not be opened more than 10 % while the heater or the humidifier is running | |

### Grow light
| # | Rule | Value |
|---|---|---|
| L1 | Run length | 1-1800 s |
| L2 | Winter top-up only: refused outside | November-February |
| L3 | Photoperiod window | 14 h centred on solar noon (the day itself when longer) |
| L4 | Refused in bright light (already bright enough) | 30 klux or more |
| L5 | Refused once today's light has reached the upper limit | 20 mol/m2 |

### Shade
| # | Rule | Value |
|---|---|---|
| X1 | Position | 0-100 % |
| X2 | Minimum time between moves (closing; opening is always allowed) | 10 min |

Tests: `test_vent_*`, `test_heater_*`, `test_humidifier_*`, `test_lamp_*`, `test_shade_dwell_and_always_allowed_to_open` (all in `backend/tests/test_rules.py`; boundaries are tested on both sides of each limit).

### The safety minimum temperature
| Season | Minimum | Effect |
|---|---|---|
| Growing | **15 C** (set by the grower) | vent may not open below it; humidifier refused below it; dashboard turns red |
| Winter rest | **10 C** (grower agreed it may fall for the nobile Dendrobium's rest) | same rules, lower value |

## 5. Hard limits in the actuator node (layer 2, independent of the backend)

| # | Rule | Value |
|---|---|---|
| A1 | Command expiry: ignored if older than (or more than 5 s from the future) | 60 s |
| A2 | Run lengths: pump / heater, humidifier, lamp | at most 120 s / 1800 s |
| A3 | Positions (vent, shade): whole number | 0-100 |
| A4 | Dose size: whole number | 1-50 mL |
| A5 | **Doser interlock** (wired to the pump relay): refused unless the pump is running | |
| A6 | The pump cannot be started while it is running (irrigation is never extended) | |
| A7 | Heater, humidifier and lamp **can** be re-triggered (they are re-commanded every interval, and a full run lasts exactly one interval); the old run is closed with what it delivered | |
| A8 | **Fail-safe:** on broker disconnect, stop pump, heater, humidifier and lamp. Vent and shade stay where they are (a stuck vent is safer than one that slams shut) | |
| A9 | Unknown device or action: refused | |

Tests: `actuators/tests/test_actuators.py` (15 tests).

## 6. Automatic control behaviour (what the controller proposes)

These are control strategies, not safety rules: every proposal still passes sections 3-4, and a refused proposal is logged.

| Device | Behaviour |
|---|---|
| **Heater** | Proportional. **Day setpoint 25 C** (21 C in the rest), full power 3 C below; desired day band 21-28 C (18-24 C in the rest, a cooler rest by the grower's decision). **Night setpoint 17 C** (growing) or **12 C** (rest), full power 2 C below, which is the season's safety minimum. After sunrise the setpoint rises gradually over 40 % of the day length (at most **5 hours**) and the heater is capped at **60 %** of full power during that warm-up; after sunset it eases down over **3 hours**. The night vent shares this setpoint, so heater and vent never fight. A run shorter than 60 s is not sent. |
| **Vent, by day** | Opens above **28 C** in proportion (30-100 %), closes below **24 C**, holds in between. Also opens to 50 % if humidity exceeds 85 % while warm. |
| **Vent, by night (growing season only)** | Opens when the air is more than 1 C above the shared setpoint, the outdoor air is at least 3 C cooler and the greenhouse is at least 3 C above the safety minimum; the opening is **sized from the outdoor reading** (cools at most 2 C per interval, at most 50 %); without an outdoor reading it uses small 10 % steps. **No night venting in the winter rest.** By day the vent opens only if the outside air is at least 2 C cooler. When outside is below the safety minimum the vent is capped at 30 %. |
| **Humidifier** | Proportional: none at 65 % humidity, full power at 50 %. Daytime only, vent closed. |
| **Grow light** | Winter top-up toward **15 mol/m2/day**: spreads the day's shortfall over the rest of the 14-hour photoperiod, sized from the light already received and the light now arriving (with the shade's and the lamp's own contribution removed). Off in bright light and once the target is reached. |
| **Shade** | Closes (100 %) when the un-shaded light is at or above **25 klux**, or when today's projected light would exceed 18 mol/m2/day; opens below 18 klux. **Summer months only, and it moves only during the day (sunrise to sunset)**: at dusk, night and dawn it holds, so a fading light cannot make it flip open and shut. |
| **Pump** | Waters when the medium has dried below **30 %** (15 % in the rest), the minimum gap has passed and fewer than 2 runs were made in 24 h; sized to reach the season's target. |
| **Doser** | **Never automatic.** Manual feeding only (section 4). |

Tests: `test_auto_*` in `backend/tests/test_rules.py`, and the closed-loop realism tests in `test_weather_and_realism.py`.

## 7. Assist (Claude): advisory only

| # | Rule |
|---|---|
| C1 | Claude cannot reach an actuator. It explains readings and proposes a watering schedule. |
| C2 | A proposed schedule is checked against the static limits: whole-second runs of at most 120 s, due within the next 24 simulated hours, at most 2 runs per 24 h, and the season's minimum gap (48 h or 168 h, counting earlier waterings). A violation marks the suggestion `rejected_by_safety`. |
| C3 | A person must approve it. Approval re-runs the same check. |
| C4 | Each scheduled run is checked again by the rules engine when it executes (moisture, stale data and so on). |
| C5 | The data sent to the model is numbers and enumerated values only, delimited as data; a reading that failed the plausibility check is labelled as such, and the model is told not to trust it. |
| C6 | If Claude is unavailable or refuses, the endpoint returns an error and the rest of the system is unaffected. Calls are limited to 10 per minute. |
| C7 | The model is `claude-sonnet-5-5` (configurable); the tool is declared with a strict schema and the model is not forced to call it (forced tool use is not supported by this model). Until an API key is set, a deterministic mock stands in. |

Tests: `test_unsafe_schedule_from_the_model_is_rejected_by_safety`, `test_suggestion_approval_and_scheduled_execution`, `test_claude_path_uses_strict_tool_without_forced_tool_choice`, `test_claude_failures_degrade_gracefully`.

## 8. Interlock matrix (what blocks what)

| If this is happening | ...this is refused |
|---|---|
| Pump not running | doser |
| Winter rest | doser, night venting, grow light outside Nov-Feb (lamp), automatic shading |
| Vent open more than 10 % | heater, humidifier |
| Heater or humidifier running | opening the vent more than 10 % |
| Temperature below the season's safety minimum | opening the vent, misting |
| Humidity at or above 80 % | misting |
| Temperature at or above 27 C | heating |
| Light at or above 30 klux, or today's light at 20 mol/m2 | the grow light |
| Stale, missing or implausible data | everything except the safe direction |

## 9. Known gaps

- **Outdoor temperature sensor, no forecast.** An outdoor reading sizes the night vent and blocks venting when outside air is not cooler; a freezing night once caused a 9 C fall in 30 minutes before this. There is no short-term forecast (decided: not needed).
- **Seasons, lamp months and shade months are calendar-based**, while the weather is not.
- **No water or nutrient tank level, no flow or leak sensing, no nutrient (EC) sensor:** the doser's effect is not measured.
- **Accounts:** open sign-up (viewer only), no email verification, no sign-up rate limit, no lock-out after failed logins, one shared API key for machines.
- **Transport security:** MQTT and HTTP are unencrypted in the demo (authentication and permissions are on).
- **Fault injection** is an open test endpoint; remove it in any real deployment.
