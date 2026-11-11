# Iteration history

How the GreenGrow demonstrator evolved on 9 November 2026, one refinement at a time. Each step follows the same pattern:
**what the grower saw or asked, what changed, the evidence, and what it left open.** The reasons behind individual design choices are in
[DESIGN_DECISIONS.md](DESIGN_DECISIONS.md); this file records the order things happened in and what each step taught.

**About the evidence.** The simulated data of each earlier version was not kept: the database was reset after most changes. The figures below come from
the checks that were run at the time (the database queries, `scripts/closed_loop_report.py`, `scripts/sampling_experiment.py` and the test output),
so they are quoted as observed then. The `docs/dashboard_nomist.png` screenshot (taken by the grower at 12:37, before the humidifier existed, not committed to the
repository) is the one saved picture of an earlier state. Everything below can be reproduced from the commit history for the code, but not for the old data.

## At a glance

| # | Trigger (what was seen or asked) | Change | Key result |
|---|---|---|---|
| 0 | Initial brief: Sense, Act, Connect, Assist | First working stack | 53 tests; closed loop proven live |
| 1 | A 999 C test reading stretched the temperature chart; "100 C is more than enough" | Fault injector capped at +/-100 C; test rows removed | Chart rescaled; -10 C plausibility floor kept for freezing |
| 2 | "The temperature swings over a large range... looks like a sensor failure" | Real weather input, thermal-mass model, heater | Daily swing cut from about 18 C to about 1.5 C; never below 17 C in a freezing fortnight |
| 3 | "Light levels seem to be swinging, I thought we had solar radiation" | Confirmed light is driven by real solar data; outside solar added to the light chart | Explained: day/night cycle plus real cloud |
| 4 | Humidity too low for orchids; "a sprayer, yes humidifier" | Humidifier actuator, proportional, daytime only | Daytime humidity 33 % to 57-62 % |
| 5 | Live testing of step 4 | Fixed overlapping heater/humidifier runs; fixed lost first telemetry | Rejected commands 4 to 0; start-up transient gone |
| 6 | Laptop overload, "my engine has stopped" | Diagnosis only | A real limitation of accelerated time, **open** |
| 7 | "Is the time unit wrong, do we need a calendar?" | Charts plot simulated time, fixed range per scenario | A 24-hour cycle is now one day on the axis |
| 8 | "Keep my orchids healthy and thriving" (Cattleya, Dendrobium) | Measurable targets, day/night schedule, winter rest, lamp and shade, manual feeding | Winter and summer: 7 of 7 targets |
| 9 | "Some sensors still cycle at high frequency, is it the calendar?" | Diagnosis (not the calendar); shade no longer flips at dusk | Old controller cycled 13-19 h; new one is daily or slower |

---

## 0. First working stack

**Asked:** four components (sense, act, connect, assist) in Docker Compose: a simulated Arduino reading soil moisture, temperature, humidity and light every
30 minutes; Django as data generator; MQTT as the broker; Swagger for the API; Grafana for the dashboard; an assistant that explains readings and suggests
watering schedules, checked by safety rules.

**Built:** Mosquitto with authentication and per-role permissions; Django generator, REST API and Swagger; edge-node and actuator simulators;
a bridge as the only publisher of commands; Postgres; Grafana with a read-only database role; the Claude assist component (mock until an API key is set).

**Evidence:** 53 unit tests passing; live checks showed the closed loop working (a pump command accepted, moisture rising on the next sample), a 500-second
pump command and a keyless request rejected, a fake 999 C sensor value blocking actuation, and all 12 dashboard queries returning data.

**Left open:** the simulated environment itself (next step).

## 1. Sensor range: the 999 C test reading

**Seen:** the fault test used to prove the plausibility rule had stored two 999 C readings, so the temperature chart scaled up to about 1000 C and flattened the real line.
The grower said 100 C was more than enough, and that the -10 C lower limit was fine because the greenhouse could go into freezing conditions.

**Changed:** the fault injector now refuses temperature values outside -100 to 100 C; the two stored test rows were deleted; the system's own plausibility range
(-10 to 60 C) was left alone.

**Evidence:** a request for 999 C now returns HTTP 400 (new test); highest stored temperature fell from 999 to 28.2 C; 42 backend tests passing.

**Taught:** a safety test can pollute the data it is protecting. The commands rejected during that test were kept as evidence; only the faulty readings were removed.

## 2. Realism: weather, thermal mass and a heater

**Seen:** the stored data showed the original generator rising 3-4 C per hour with the sun, falling 2 C per hour after the afternoon, and cooling to about 10 C at night
(the made-up outside air). Over the first three simulated days the greenhouse swung roughly 10 to 28 C; the largest change between two 30-minute samples was 2.7 C.
The grower: *"this would point towards a sensor failure... temperatures would slowly increase until the hottest time of the day... maintain... and slowly drift down."*

**Asked and decided (with the grower):** a real meteorological input for Cranfield, UK, downloaded once and cached; outside temperature, humidity and solar radiation;
and, because the crop is delicate tropical orchids that must not approach freezing, a heater as a fourth actuator and a 15 C safety minimum.

**Changed:** the generator now takes real hourly weather (Open-Meteo) and models a greenhouse with thermal mass, a vent and a heater. The winter scenario is the coldest
fortnight of 2025/26 (from 29 December: minimum -5.2 C, 115 hours below 0 C); a June fortnight is the summer scenario.

**First attempt, and why it was not enough.** An on/off heater (heat when below 16 C) on the winter weather held the greenhouse between 14.6 and 18.8 C: it dipped
**below the 15 C safety minimum** on the coldest night, and cycled, with a mean 30-minute change of 1.63 C and a largest of 2.52 C. That is nearly as jumpy as the model it replaced.

**Second attempt:** proportional heater control (run length grows smoothly below 20 C, full power at 16 C) and a better-insulated structure.

| Scenario | Greenhouse air | Largest 30-min change | Mean 30-min change |
|---|---|---|---|
| Original generator (first three days) | about 10 to 28 C | 2.7 C | 0.69 C |
| Winter, on/off heater | 14.6 to 18.8 C (below the 15 C minimum) | 2.52 C | 1.63 C |
| **Winter, proportional heater** | **17.1 to 18.6 C** with -5.2 C outside | **0.23 C** | **0.03 C** |
| **Summer** | 18.8 to 24.5 C; peak about 14:00-16:00, gentle evening decline | 0.78 C | 0.14 C |

**Evidence in the tests:** realism tests now run the controller on both real weather files and assert the winter minimum, the smoothness, and the afternoon peak (77 tests passing).
**Also re-done:** the sampling-interval experiment was re-run on the new model, because the old numbers no longer applied. At 30 minutes the temperature interpolation error is 0.010 C and a
morning warm-up is noticed 10 minutes late on average (11 events).

**Taught:** the realism problem was the physics model, not noise. A control strategy can also be the source of the "swing": the first heater fixed the minimum and created a new jitter.

**Left open:** winter humidity averaged about 33 %, far below the orchids' 50-80 % band (next steps).

## 3. The light chart

**Seen:** *"light levels seem to be swinging at the moment, I thought we had solar radiation too."*

**Checked:** the light sensor already came from the real hourly solar radiation (through the glazing at 70 %, converted to lux). The data showed why it looks lively: a daily 0-to-peak-to-0 curve,
real cloud (winter daily peaks ranged 65-242 W/m2), and, at 180x demo speed, each simulated day passing in about 8 real minutes.

**Changed:** nothing in the data. The outside solar radiation was added to the light chart as a second, dashed series so the link is visible.

**Taught:** a correct signal can still look wrong if the dashboard does not show its cause.

## 4. Humidity and the humidifier

**Seen:** with the heater working, winter air was dry: 23-55 % humidity, mean about 33 %, against 50-80 % for orchids (heating cold outside air dries it).
The grower: *"...a sprayer, yes humidifier."* Options were offered; the grower chose a **new fifth actuator** (a fine-mist fogger) alongside the irrigation pump, proportional control, and daytime only with lockouts.

**Changed:** a humidifier that runs in proportion to how far humidity is below 65 % (full at 50 %), only 07:00-17:00 on the simulated clock, and refused at 80 % humidity, below 15 C, or while the vent is open.

| | Daytime humidity | Night-time humidity |
|---|---|---|
| Winter, before the humidifier | about 33 % average (23-55 %) | about the same |
| **Winter, with the humidifier** | **57-60 %** | 25-41 % |
| Live system, by hour (data so far) | 61-62 % from 09:00 to 16:00 | 33-40 % from 00:00 to 07:00 |

**Evidence:** 86 tests passing; in the live system humidity climbs from about 36 % to 66 % in the first two samples after 07:00, then holds near 62 %.

**Taught:** fixing one variable exposes a trade-off. Because wet leaves overnight invite rot, misting is daytime only, so night humidity in winter is still low. That was kept as a documented limitation, not hidden.

## 5. Faults found in live testing

Running the whole stack (not just the unit tests) at 3-second samples found two real problems.

1. **Overlapping runs.** Four commands (two heater, two humidifier) were rejected by the actuator node as "already running". A full-power run lasts exactly one sampling interval, so the next command can arrive just before the last run ends,
   and a whole interval went unheated (temperature dips to 17.2 C). **Fix:** the heater and humidifier can be re-triggered (the old run is closed with what it delivered); the irrigation pump deliberately cannot. Re-run: 0 rejections.
2. **Lost first telemetry.** The first one or two samples were being dropped because the edge node started publishing before the bridge had subscribed, so the first heater commands were never issued. The first stored readings
   were 16.84 and 16.40 C, then 18.32 C (a 1.92 C jump). **Fix:** the bridge only reports healthy once it is subscribed, and the edge node waits for it. Re-run: first reading 17.69 C, minimum 17.7 C, largest step 0.74 C, no rejections.

**Evidence:** 87 tests passing (a new test covers the re-trigger rule).
**Taught:** unit tests were green throughout; both faults only appeared when the real containers and timing ran together.

## 6. Version control and the first overload (open)

**Versioning:** the code was committed (`b62494d`, 72 files, no secrets) on `main`, a `dev` branch was created, and a private repository was created at `amc-dats/greengrow-smart-greenhouse`, with both branches pushed.

**Seen:** the laptop ran out of headroom (about 550 MB free, Docker's VM at a load average near 50) and Docker stopped responding; the dashboard stopped updating. After a restart the data showed a few readings **below the 15 C
safety minimum (lowest 14.6 C)**, in the minutes when samples arrived in bursts, one to two seconds apart instead of ten.

**Explained:** at 180x demo speed a heater run takes about 10 real seconds, so when samples bunch up the next command arrives before the previous run has reported back and that interval is under-heated. It does not occur in the real
30-minute rhythm; the clean-machine results above (never below 17 C) are unaffected.

**Status: open.** Three options were put to the grower: lower the demo speed with a longer health-check timeout, apply heater and humidifier effects as soon as a command is accepted, or document it. **No decision yet, and nothing has been changed.**

**Taught:** accelerating simulated time ties correctness to wall-clock timing, which fails under load. This belongs in the report as a limitation of the demonstration method, not of the greenhouse design.


## 7. The dashboard time axis

**Seen:** *"either the time unit is incorrect on the dashboard or the cycling is not showing a 24h cycle. Do we need to apply a calendar?"*

**Found:** the data was right and the axis was misleading. Every reading already carries a simulated calendar time, and the hourly humidity profile shows a clean 24-hour pattern, but the dashboard plotted the **wall-clock receive time**.
At 180x demo speed one simulated day takes 24 h / 180 = 8 real minutes, so a day appeared as a fast pulse on an axis labelled in minutes (in the grower's screenshot the light peaks are about 8 minutes apart).

**Changed (the grower chose the simulated calendar axis over day markers on the live view):** the charts now plot the simulated time (UTC), with the dashboard time range fixed to the weather period (winter: 29 Dec 2025 to 12 Jan 2026; summer: 1 to 15 June 2026).
There is one dashboard per scenario, and Grafana opens the one matching `WEATHER_SCENARIO`. The lines fill in from the left as the simulation advances, like a recording.

**Taught:** time compression has to be visible to the reader. When simulated time and wall-clock time differ, the axis must say which one it shows. The cost is that the calendar dashboards are tied to a fixed weather period, so a new period needs a new dashboard range.

---

## Test count over time

| After step | Backend | Actuators | Edge | Total |
|---|---|---|---|---|
| 0 First stack | 41 | 9 | 3 | 53 |
| 1 Fault cap | 42 | 9 | 3 | 54 |
| 2 Weather and heater | 63 | 11 | 3 | 77 |
| 4 Humidifier | 71 | 12 | 3 | 86 |
| 5 Live-testing fixes | 71 | 13 | 3 | 87 |

## What the sequence shows

Each refinement came from the grower looking at the running system and asking a sharp question. Four of the six changes were a mismatch between what the model produced and what a person would expect to see
(swinging temperature, swinging light, dry air, a stray 999 C), and two were found only by running the full stack. The recurring lesson is that realism is as much about the controller and the dashboard
as about the physics, and that every fix should be checked against the real system, not just the tests.

## 8. From "safe" to "thriving": orchid targets (10 November)

**Asked:** the module decks were added (GreenGrow is the module's running case; the evidence it asks for is a hazard register, assurance case, test plan, twelve-factor scorecard and AI evaluation cases), and the goal was stated: keep delicate orchids healthy and thriving outside their native habitat. The orchids are **Cattleya and the deciduous nobile type of Dendrobium**.

**Found (measured on the real weather, before any change):** the greenhouse held the orchids safe but did not give them what they need. In winter the day and night temperature were nearly the same (0.4 C apart), the daily light was 4.2 mol/m2/day against a 12-20 band (0 of 14 days in band), and in summer the light was 27.5 (peak 71.5 klux against a 37.7 limit) and the heater ran all night.

**Changed (each choice agreed with the grower):** measurable targets T1-T7 (docs/ORCHID_TARGETS.md); a day/night temperature schedule with a gentle 5-hour morning warm-up; a winter rest (December-February) with a lower 10 C safety minimum and almost no water; dry-down watering; a winter-only grow light and a summer-only shade; manual feeding; night venting in small steps in the growing season only.

**Evidence:** winter and summer both pass 7 of 7 targets (summer daily light is the tightest, 86 % of days against an 80 % bar). 105 tests pass.

**Found along the way (testing against the real weather, not the unit tests):** (1) opening the vent at night with freezing air outside dropped the greenhouse 9 C in 30 minutes, to 3 C, below the safety floor, because the controller has no outdoor sensor: night venting was restricted to the growing season and to 10 % steps; (2) the heater's full-power morning warm-up caused a 2.9 C rise in half an hour: the setpoint now ramps over 5 hours with the heater capped at 60 %.

**Open:** whether to add an outdoor sensor (and later a short-term forecast) so seasons and venting adapt to the actual weather; whether the lamp may run on dull summer days.

## 9. "Some sensors still cycle at a high frequency, is it the calendar?" (10 November)

**Measured on the running stack's data:** light cycles once every 24.8 simulated hours, exactly right, so the calendar is correct. The others cycled faster than a day: temperature about every 13 hours, humidity about every 14, soil moisture every 19. Those were control-loop oscillations of the old controller (frequent small waterings, a heater that did not distinguish day from night).
**Measured on the new controller in the model:** soil moisture now cycles weekly in winter and every 2 days in summer; winter temperature is a clean 24-hour cycle. Humidity still shows two rises and falls a day, which is physics (warmer air holds less relative humidity, so it dips as the heater warms the air and rebounds when the heater stops at 19:00), not chatter.
**One real chatter found and fixed:** at dusk in summer the shade opened and shut again within half an hour as the light faded. It now moves only during the day. Summer daily light improved from 71 % to 86 % of days in band as a side effect.
**Also true:** a 14-day dashboard compresses each daily cycle into one of fourteen humps, which looks like high-frequency cycling; and the live stack still runs the old controller until it is restarted on the new code (not done: it needs the grower's decision).

## 10. "Why vent at night?" and following the sun (10 November)

**Asked:** why vent at night (heater overshoot?), and whether day/night should track sunrise and sunset since a long summer day would still count as daytime.

**Answered with evidence:** night venting sheds stored daytime heat so the night drops into the night band (a flowering cue); it is growing-season only. A fixed 07:00-19:00 day is wrong at Cranfield: 7.8 h of daylight on 29 December, 16.4 h on 1 June.

**Changed (agreed):** an outdoor temperature sensor (no forecast) sizes the night vent; heater and vent share one setpoint with a dead band (refused proposals fell from 50 to 0, biggest 30-minute step from 3.9 C to 2.2 C); day and night are now computed from the date and location (`greenhouse/solar.py`): warm-up starts at sunrise (40 % of the day length, at most 5 h), the evening ease-down starts at sunset, misting runs from sunrise to 2 h before sunset, the grow light tops the day up to a 14-hour photoperiod centred on solar noon, and the shade moves between sunrise and sunset.

**Found by re-running the targets:** on short winter days the heater cannot reach 21 C (daytime in band only 55-65 % against 80 %). Agreed fix: a cooler **winter-rest day band of 18-24 C** (setpoint 21 C), which suits the nobile rest; it now passes (97 %). Summer light: tightening the shade's look-ahead lifted days in band from 71 % to **79 %, still one day short of the 80 % bar** (the 50 % shade cannot stop the two brightest days; the lamp is winter-only by decision). Left as a documented shortfall.

**Operations:** snapshot taken first (`snapshots/greengrow_20261110_1233.sql`), then the stack was rebuilt and restarted without removing volumes; the data was intact (1,333 readings afterwards, 1,205 before).

## 11. Accounts for browser testing (10 November)

**Asked:** do we have authentication, and add registration and authentication where needed for Playwright testing.

**Found:** broker logins and ACLs, an API key for writes, a simulator token and localhost-only ports existed; reads were open and there were no user accounts.

**Changed (agreed: web UI + API, viewer/grower roles, all reads signed in):** Django session login, open registration (always a viewer), `/app/` console page, roles viewer / grower / admin (`manage.py set_role`), CSRF on session writes, 10-character passwords, 8-hour sessions; the API key still works for machines (as admin); `/health/` stays open. 11 account tests added (110 pass); Playwright tests are in `e2e/`.

**Not done / to say in the report:** no sign-up rate limit or lock-out, no email verification, no TLS. The Playwright image was not pulled or run in this session.

## 12. Cold greenhouse, dry soil, one dashboard (10 November)

**Asked:** "it is cold and the soil is dry", then "the scale on the dashboard makes it confusing", then "I only want one dashboard".

**Found:** cold was the agreed winter rest (nights 11-12 C, afternoon peak 19 C with -4 C outside), kept by choice. Soil read about 0 % for days: the model dried the medium in about a day, and the rest rule then waits 168 h between waterings. Light was not missing: the dashboards were on a fixed range (29 Dec - 12 Jan) while the simulation was in early February, and an old wall-clock dashboard (`ts`, last 6 h) squashed about 45 simulated days into one view.

**Changed (agreed):** a drier medium now evaporates more slowly (full rate above 40 %, down to 10 % of it), so the dry-down takes about a week; light chart on one lux axis with a glare-limit line; **one dashboard** (`grafana/dashboards/greengrow.json`) with a rules text box, a season box and season-aware limit lines (safety minimum 10/15 C, watering 15/30 % and target 40/60 %); the old overview and the winter/summer pair removed (copies in `snapshots/dashboards_before_merge/`); `scripts/dashboard_link.sh` gives a link on the latest 14 simulated days (the range does not follow the simulation by itself).

**Still open:** soil still bottoms at 0-2 % for the last days of each 168 h rest wait; summer daily light 79 % against 80 %; the dashboard range is not automatic; host CPU is shared with OneDrive sync and the editor; nothing is committed.
