"""Safety limits and control targets for the GreenGrow actuators.

CROP: Cattleya and Dendrobium orchids (Dendrobium of the deciduous "nobile" type). The values below are guideline
figures gathered from orchid-society sources, which disagree with each other at the margins; they are NOT from a
standard. See docs/ORCHID_TARGETS.md for the sources and the reasoning. Change them here; every service imports this file.
All durations that involve the greenhouse are in SIMULATED time, and the clock is UTC.
"""

# =============================================================== temperature (C, greenhouse air)
# SAFETY MINIMUM (set by the grower): the vent may not open below it and the dashboard turns red. 15 C normally; the grower
# agreed it may fall to 10 C during the winter rest, which the deciduous nobile Dendrobium needs (sources: nights 10-13 C).
# Cattleya prefer to stay above 12.8 C and tolerate lower, so they sit at the cool end of their range during the rest.
COLD_DANGER_C = {"growing": 15.0, "rest": 10.0}
DAY_HOURS = (7, 19)            # "day" on the simulated clock; the rest is night
DAY_BAND_C = (21.0, 28.0)      # desired daytime range (Cattleya 21-29 C, i.e. 70-85 F): the heater lifts it, the vent caps it
NIGHT_BAND_C = {"growing": (15.0, 18.0), "rest": (10.0, 14.0)}   # desired night low by season (sources: Cattleya 13-16 C, nobile rest 10-13 C, up to 16 C in some)
DAY_NIGHT_DIFF_MIN_C = 5.0     # desired day-minus-night difference (sources: roughly 5-11 C)

# Heater: proportional control, with a different setpoint by day and by night (and by season at night).
HEATER_DAY_SETPOINT_C = 25.0    # no heating at or above this by day; proportional control settles about 2-3 C below it
HEATER_DAY_BAND_C = 3.0
HEATER_NIGHT_SETPOINT_C = {"growing": 17.0, "rest": 12.0}   # full heating 2 C lower, i.e. at the season's safety minimum
HEATER_NIGHT_BAND_C = 2.0
HEATER_RAMP_HOURS = 5.0         # after dawn the setpoint rises from the night to the day value over this many hours
HEATER_RAMP_MAX_DUTY = 0.6      # and during that warm-up the heater runs at no more than this share of full power (a gentle rise)
HEATER_MAX_TEMP_C = 27.0        # hard limit: the rules refuse any heating at or above this
HEATER_MAX_RUN_S = 1800         # one sampling interval; it must be re-commanded to continue
HEATER_VENT_MAX_OPEN_PCT = 10   # no heating while the vent is open more than this

# Vent: holds the daytime plateau.
VENT_OPEN_TEMP_C = DAY_BAND_C[1]
VENT_CLOSE_TEMP_C = 24.0
VENT_OPEN_HUMIDITY = 85.0       # also open (when warm enough) above this relative humidity
VENT_MIN_DWELL_MIN = 10.0

# =============================================================== humidity
HUMIDITY_BAND = (50.0, 80.0)    # desired relative humidity (all sources)
HUMIDIFIER_TARGET_RH = HUMIDITY_BAND[0] + 15.0   # 65 %: no misting at or above this
HUMIDIFIER_PROP_BAND_RH = 15.0                    # full misting at or below 50 %
HUMIDIFIER_MAX_RH = HUMIDITY_BAND[1]              # hard limit: refused at or above 80 %
HUMIDIFIER_MAX_RUN_S = 1800
HUMIDIFIER_HOURS = (7, 17)                        # daytime only, so leaves dry before night
HUMIDIFIER_VENT_MAX_OPEN_PCT = 10

# =============================================================== light
LUX_PER_UMOL = 54.0             # sunlight: about 54 lux per umol/m2/s of plant-usable light (an estimate)
DLI_BAND = (12.0, 20.0)         # mol/m2/day acceptable for both genera: Cattleya 12-18 and nobile Dendrobium 14-20 (the ideal overlap is 14-18)
DLI_IDEAL = (14.0, 18.0)
DLI_TARGET = 15.0               # the grow light tops the day up to this
PEAK_LUX_MAX = 37700.0          # Cattleya greenhouse upper limit (3,500 foot-candles); shade above about 30 klux
SHADE_TRANSMISSION = 0.5        # a 50 % shade cloth: fully deployed it lets half the light through
SHADE_CLOSE_ABOVE_LUX = 25000.0
SHADE_OPEN_BELOW_LUX = 18000.0
SHADE_MIN_DWELL_MIN = 10.0
LAMP_MONTHS = (11, 12, 1, 2)    # the grow light is a winter top-up for the long darkness, not used the rest of the year
SHADE_MONTHS = (5, 6, 7, 8)     # the shade is for very strong summer light, not used the rest of the year
LAMP_HOURS = (7, 21)            # photoperiod window (14 h)
LAMP_PPFD = 300.0               # umol/m2/s of the grow light at full power (about 16 klux)
LAMP_MAX_RUN_S = 1800
LAMP_OFF_ABOVE_LUX = 30000.0    # no supplementary light in bright sun
DEFAULT_INTERVAL_S = 1800.0     # one sampling interval (30 simulated minutes), for light integration

# =============================================================== watering (dry-down cycle with a seasonal rest)
# Orchid roots should nearly dry between waterings; the deciduous nobile Dendrobium wants almost no water in winter.
REST_MONTHS = (12, 1, 2)        # winter rest (simulated calendar)
MOISTURE_AUTO_START = {"growing": 30.0, "rest": 15.0}   # irrigation is triggered below this
MOISTURE_TARGET = {"growing": 60.0, "rest": 40.0}       # irrigation is sized to reach this; runs refused at/above it
PUMP_MIN_GAP_H = {"growing": 48.0, "rest": 168.0}       # minimum time between waterings
MOISTURE_CEILING = 70.0         # no run may be projected to exceed this
PUMP_MAX_RUN_S = 120
PUMP_MAX_RUNS_PER_DAY = 2
SOAK_CONTINUE_WINDOW_H = 1.5    # a watering is one soak of up to PUMP_MAX_RUNS_PER_DAY runs; a run this soon after the last continues it, so it is not held by the dry-down gap
PUMP_NOMINAL_GAIN_PCT_PER_S = 0.15

# =============================================================== nutrient dosing pump (not in the winter rest)
DOSE_MAX_ML = 50
DOSE_MAX_ML_PER_DAY = 200
DOSE_MIN_GAP_H = 12.0
DOSE_DEFAULT_ML = 20

# =============================================================== sensor plausibility (physical sanity ranges, not crop ranges)
PLAUSIBLE = {
    "soil_moisture": (0.0, 100.0),
    "temperature": (-10.0, 60.0),
    "humidity": (0.0, 100.0),
    "light": (0.0, 120000.0),
}

# =============================================================== data freshness and command lifetime
STALE_AFTER_INTERVALS = 2   # no actuation if the latest reading is older than 2 sampling intervals
COMMAND_TTL_S = 60          # actuators ignore commands older than this (wall-clock seconds)

# =============================================================== night cooling and daily-light projection
NIGHT_VENT_OPEN_MARGIN_C = 1.0  # at night (growing season only) the vent opens when the air is this far above the top of the night band
NIGHT_VENT_CLOSE_MARGIN_C = 0.0 # ... and closes again once it is back inside the band
LIGHT_PROJECTION_FACTOR = 0.8   # share of the current light rate expected over the rest of the day (the sun declines)
NIGHT_VENT_MAX_POS = 10         # night cooling opens the vent only a little: there is no outdoor sensor, so it moves in small steps
NIGHT_VENT_MIN_ABOVE_FLOOR_C = 3.0   # and only while the air is at least this far above the season's safety minimum

# =============================================================== outdoor temperature (from the edge node's outdoor sensor)
OUTSIDE_PLAUSIBLE = (-40.0, 50.0)         # an outdoor value outside this is ignored (the controller falls back to the no-outdoor rules)
VENT_MIN_OUTSIDE_DELTA_C = 2.0            # by day the vent opens only if the outside air is at least this much cooler
NIGHT_VENT_MIN_OUTSIDE_DELTA_C = 3.0      # at night, likewise
VENT_NOMINAL_EXCHANGE_PER_MIN = 1 / 20    # share of the inside/outside difference removed per minute with the vent fully open (nominal)
NIGHT_VENT_SIZED_MAX_POS = 50             # the largest opening the controller will size from the outdoor reading
NIGHT_TARGET_BELOW_BAND_TOP_C = 1.0       # night cooling aims this far under the top of the night band
VENT_COLD_OUTSIDE_MAX_POS = 30            # RULE: when the outside air is below the safety minimum the vent may open at most this far
NIGHT_VENT_MAX_DROP_C = 2.0               # night cooling aims to remove at most this much heat per sampling interval (a slow evening drift)
HEATER_EVENING_RAMP_HOURS = 3.0           # after dusk the heater setpoint eases from the day to the night value over this many hours

# =============================================================== sunrise and sunset (day and night follow the sun)
# "Day" is sunrise to sunset, computed from the date and the site location (see greenhouse/solar.py). The fixed hours DAY_HOURS,
# HUMIDIFIER_HOURS and LAMP_HOURS above are only a fallback if no sun times are available.
SITE_LATITUDE = 52.06                    # Cranfield, UK (the weather file's own coordinates are used when present)
SITE_LONGITUDE = -0.66
PHOTOPERIOD_H = 14.0                     # total light per day the grow light tops up to, centred on solar noon
HUMIDIFIER_END_BEFORE_SUNSET_H = 2.0     # misting stops this long before sunset so leaves dry before night
WARM_UP_SHARE_OF_DAYLIGHT = 0.4          # the morning warm-up lasts this share of the day length, at most HEATER_RAMP_HOURS

# Winter rest (Dec-Feb): cooler days, by the grower's decision (Dendrobium nobile wants a cool rest, and the short winter days cannot
# heat the greenhouse to the growing-season band anyway).
DAY_BAND_REST_C = (18.0, 24.0)
HEATER_DAY_SETPOINT_REST_C = 21.0
