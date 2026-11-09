"""Safety limits and control targets for the GreenGrow actuators.

CROP: delicate tropical (warm-growing) orchids, e.g. Phalaenopsis. The values below are guideline figures
gathered from orchid-society sources, which disagree with each other at the margins; they are NOT from a
standard. Sources used: AOS "Cold Tolerance of Warm Growing Orchids" (2024), NYBG/AOS temperature table
(warm class: 24-29 C by day, minimum ~18 C at night), Iowa State Extension and RNZIH moth-orchid guides
(chilling injury risk below ~12-13 C, humidity roughly 50-80 %). Change them here; every service imports this file.
All durations that involve the greenhouse are in SIMULATED time.
"""

# --- orchid temperature targets (C, air temperature inside the greenhouse) ---
COLD_DANGER_C = 15.0          # SAFETY MINIMUM (set by the grower): vent may not open below it, dashboard turns red
NIGHT_MIN_C = 18.0            # recommended night minimum for warm-class orchids (guideline)
HEATER_TARGET_C = 20.0        # heater setpoint: no heating at or above this
HEATER_PROP_BAND_C = 4.0      # proportional band: full heating at or below (setpoint - band) = 16 C, a margin above 15 C
HEATER_MAX_TEMP_C = 22.0      # hard limit: the rules refuse any heating at or above this
DAY_BAND_C = (24.0, 28.0)     # desired daytime band (vent holds it)
HUMIDITY_BAND = (50.0, 80.0)  # desired relative humidity; there is no humidifier, shown on the dashboard

# --- soil moisture (%) ---
MOISTURE_AUTO_START = 30.0  # automatic irrigation is triggered below this
MOISTURE_TARGET = 60.0      # irrigation is sized to reach this; runs refused at/above it
MOISTURE_CEILING = 70.0     # no run may be projected to exceed this

# --- irrigation pump ---
PUMP_MAX_RUN_S = 120
PUMP_MAX_RUNS_PER_DAY = 4
PUMP_MIN_GAP_H = 3.0
PUMP_NOMINAL_GAIN_PCT_PER_S = 0.15  # calibrated flow: moisture gained per second of pumping

# --- vent motor ---
VENT_OPEN_TEMP_C = DAY_BAND_C[1]    # open above the top of the day band
VENT_CLOSE_TEMP_C = DAY_BAND_C[0]   # close below the bottom of the day band
VENT_OPEN_HUMIDITY = 85.0           # also open (when warm enough) above this relative humidity
VENT_MIN_DWELL_MIN = 10.0

# --- heater ---
HEATER_MAX_RUN_S = 1800                     # one sampling interval; it must be re-commanded to continue
HEATER_VENT_MAX_OPEN_PCT = 10               # no heating while the vent is open more than this

# --- humidifier (fine-mist fogger: adds water vapour to the air; does not wet leaves or the potting medium) ---
HUMIDIFIER_TARGET_RH = HUMIDITY_BAND[0] + 15.0   # 65 %: no misting at or above this
HUMIDIFIER_PROP_BAND_RH = 15.0                    # proportional band: full misting at or below 50 %
HUMIDIFIER_MAX_RH = HUMIDITY_BAND[1]              # hard limit: refused at or above 80 %
HUMIDIFIER_MAX_RUN_S = 1800                       # one sampling interval; must be re-commanded to continue
HUMIDIFIER_HOURS = (7, 17)                        # daytime only (simulated clock, UTC): leaves dry before night
HUMIDIFIER_VENT_MAX_OPEN_PCT = 10                 # refused while the vent is open more than this

# --- nutrient dosing pump ---
DOSE_MAX_ML = 50
DOSE_MAX_ML_PER_DAY = 200
DOSE_MIN_GAP_H = 12.0
DOSE_DEFAULT_ML = 20

# --- sensor plausibility (physical sanity ranges, not crop ranges) ---
PLAUSIBLE = {
    "soil_moisture": (0.0, 100.0),
    "temperature": (-10.0, 60.0),
    "humidity": (0.0, 100.0),
    "light": (0.0, 120000.0),
}

# --- data freshness and command lifetime ---
STALE_AFTER_INTERVALS = 2   # no actuation if the latest reading is older than 2 sampling intervals
COMMAND_TTL_S = 60          # actuators ignore commands older than this (wall-clock seconds)
