# Orchid targets: what "healthy and thriving" means, measurably

**Goal.** Keep Cattleya and Dendrobium orchids healthy and thriving in an artificial environment, outside their native habitat
(Cranfield, UK: freezing winters, weak winter light, strong summer sun).

A goal like "thriving" cannot be tested. The module says the same of "fast" or "easy": system tests need measurable objectives.
This document turns the goal into seven pass/fail targets (T1 to T7), each with a threshold, a source basis and a measurement.
They are the objectives for the system tests and the claims for the assurance case. The rules that keep the greenhouse inside them
are in [SAFETY_RULES.md](SAFETY_RULES.md).

## 1. The crop and its one real conflict

| | Cattleya | Dendrobium, **nobile type** (deciduous) |
|---|---|---|
| Day | 21-29 C (70-85 F) | warm days |
| Night | 13-16 C (55-60 F) | **10-13 C (50-55 F) for about a month** (the "winter rest") sets the buds |
| Light | 21.5-37.7 klux in a greenhouse (2,000-3,500 foot-candles); 12-18 mol/m2/day | 16-32 klux, filtered; 14-20 mol/m2/day |
| Water | roots should nearly dry between waterings | almost none in the winter rest |
| Feed | dilute, "weakly, weekly" | stop during the rest |
| Humidity | about 50-80 %, with good air movement | similar |

**The conflict:** the nobile Dendrobium wants nights of 10-13 C for its rest, but the grower's safety minimum for the delicate orchids is
15 C. **Decision (grower): the minimum may fall to 10 C during the winter rest (December to February)**, and nobile watering is cut to almost nothing
(the sources say that where the cool nights cannot be fully provided, growing the plant drier through autumn and winter often works). Cattleya prefer to stay
above 12.8 C and tolerate lower, so they sit at the cool end of their range during the rest. This is a recorded trade-off, not a solved problem.

**Sources.** These are orchid-society and grower pages, not peer-reviewed work, and they disagree at the margins. The values are guidelines, not a standard.
[AOS Cattleya culture](https://www.aos.org/cattleya-culture-part-2) · [AOS Dendrobium culture sheet](https://www.aos.org/orchid-care/care-sheets/dendrobium-general-culture-sheet) ·
[AOS Principles of Light](https://www.aos.org/principles-of-light) · [AOS cold tolerance of warm growing orchids](https://www.aos.org/orchids/articles/cold-tolerance-of-warm-growing-orchids) ·
[AOS: fertilize weakly, weekly](https://www.aos.org/orchids/articles/fertilize-weakly-weekly).

## 2. The seven targets

Measured by `scripts/orchid_report.py` and asserted in `backend/tests/test_weather_and_realism.py`, over 14 simulated days of real Cranfield weather
(the first hour, the start-up, is skipped). "Day" is sunrise to sunset for Cranfield on the simulated date (UTC; computed, see SAFETY_RULES.md); the winter rest is December to February.

| # | Target | Pass threshold | Basis | Winter (coldest fortnight, rest) | Summer (June, growing) |
|---|---|---|---|---|---|
| **T1** | Temperature never below the season's **safety minimum**: 15 C, or 10 C in the winter rest | **100 %** of samples | grower's minimum | **PASS** (lowest 11.4 C with -5.2 C outside) | **PASS** (lowest 16.8 C) |
| **T2** | Each **night's low** (3 h after sunset to sunrise) inside the season's night band: 15-18 C, or 10-14 C in the rest | 80 % of nights | Cattleya 13-16 C, nobile rest 10-13 C | **PASS** 100 % | **PASS** 93 % |
| **T3** | **Daytime** temperature (after the warm-up plus an hour, until sunset) inside the season's day band: 21-28 C, or **18-24 C in the rest** | 80 % of samples | Cattleya 21-29 C; nobile cool rest | **PASS** 97 % | **PASS** 100 % |
| **T4** | **Day-night difference** (afternoon mean minus the deep-night mean) of at least 5 C | 80 % of days | sources: about 5-11 C | **PASS** 93 % (6.5 C) | **PASS** 100 % (6.6 C) |
| **T5** | **Daytime humidity** (misting window, from an hour after sunrise) inside 50-80 % | 90 % of samples | all sources | **PASS** 100 % | **PASS** 100 % |
| **T6** | **Daily light** inside 12-20 mol/m2/day, acceptable for both genera (ideal overlap 14-18) | 80 % of days | AOS Principles of Light | **PASS** 100 % (mean 15.3) | **FAIL** 79 % (11 of 14 days; range 8.8 to 22.7) |
| **T7** | **Light never glaring**: at most 37.7 klux | 95 % of samples | Cattleya greenhouse maximum | **PASS** 100 % | **PASS** 100 % |

**Summer daily light is the tightest target.** With the grow light winter-only, a dull June day cannot be topped up (the dullest was 8.8 mol/m2/day), and the 50 % shade
cannot stop the two brightest days reaching 21-23. It reaches 79 % of days, one day short of the 80 % bar (after tightening the shade's look-ahead). If it matters: allow the lamp on dull summer days, or add a denser shade.

### Supporting targets (monitored, not pass/fail)
- **Watering follows a dry-down cycle**: roots dry between waterings (growing season: water below 30 % medium moisture and at least 48 h apart; winter rest: below 15 % and at least 168 h apart).
- **No fertiliser in the winter rest.** Feeding is manual, "weakly, weekly" (see SAFETY_RULES.md, section 6).
- **Daytime humidity** is held by the humidifier; **night humidity in winter is not** (misting is daytime only), so winter nights are dry (about 25-40 %). Not a target, a documented limitation.

## 3. How far the greenhouse was, and is now (real Cranfield weather)

| | Target | Before (flat 18-20 C, no light control) | After: winter | After: summer |
|---|---|---|---|---|
| Day temperature | 21-28 C (18-24 C in the rest) | 18.2 C winter, 21.6 C summer | 18.9 C | 24.2 C |
| Night low | 15-18 C (10-14 C in the rest) | 17.7 C winter, 19.3 C summer (too warm, and heated all night) | 11.7 C | 16.9 C |
| Day-night difference | at least 5 C | 0.4 C winter, 2.2 C summer | 6.5 C | 6.6 C |
| Daily light | 12-20 mol/m2/day | **4.2** winter (0 of 14 days in band), **27.5** summer (too bright; peak 71.5 klux) | 15.3 | 17.5 |
| Daytime humidity | 50-80 % | 61 % winter, 65 % summer | in band | in band |

## 4. What provides each target

| Target | Provided by |
|---|---|
| T1 safety minimum | heater (proportional, night setpoint 17 C, 12 C in the rest), cold lock-out on the vent, safety rules |
| T2, T4 night drop | the night setpoint, the thermal mass, night venting sized from the outdoor reading in the growing season |
| T3 day band | the heater's day setpoint (25 C; 21 C in the rest), reached after a gentle warm-up from sunrise, the vent above 28 C |
| T5 humidity | the daytime-only fine-mist humidifier (proportional, 65 % target) |
| T6, T7 light | the grow light (winter top-up to 15 mol/m2/day) and the shade (summer, strong light) |

## 5. Assumptions and limits (say these in the report)

- **Simulated, not physical.** The greenhouse model is simple and not calibrated to a building; every coefficient is a plausible round number.
- **Daily light is estimated.** Plant-usable light is taken as lux divided by 54 (an approximation for sunlight, 54 lux per umol/m2/s); the real value depends on the light source.
- **Guideline values, not a standard.** Sources disagree: for example the lower night limit for Cattleya, and nobile rest temperatures.
- **Seasons are calendar-based** (the rest is December-February, the lamp November-February, the shade May-August). Real weather is not: a warm February or a cold May would be handled by the temperature rules but not by these calendar choices. The outdoor sensor lets venting adapt to the actual weather, but the seasons themselves do not.
- **Outdoor temperature sensor only, no forecast** (decided). Night venting is sized from it and runs in the growing season only (a freezing night once caused a 9 C fall in 30 minutes before this).
- **Sunrise and sunset are computed** (NOAA approximation, a few minutes accurate) from the date and the weather file's coordinates; clouds are not considered.
- **Real-hardware behaviour, long-term plant response and Claude's advice quality are not evaluated.** The targets describe conditions, not plant outcomes.

## 6. Reproduce

```bash
docker run --rm -i -v "$PWD:/w:ro" -e PYTHONPATH=/w/backend:/w/common python:3.12-slim python - winter < scripts/orchid_report.py   # or: summer
docker compose run --rm --no-deps api python -m pytest -q tests/test_weather_and_realism.py
```
