"""Django settings for the GreenGrow backend (data generator, REST API, MQTT bridge, assist)."""
import os
from datetime import datetime
from pathlib import Path

from greengrow_common import safety_limits as L

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,api,testserver,.trycloudflare.com").split(",")
# Public demo behind a Cloudflare tunnel: https is terminated by the tunnel and forwarded as X-Forwarded-Proto.
CSRF_TRUSTED_ORIGINS = ["https://*.trycloudflare.com"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
PUBLIC_HTTPS = os.environ.get("PUBLIC_HTTPS", "0") == "1"   # set while the app is reachable from the internet
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = PUBLIC_HTTPS
SECURE_HSTS_SECONDS = 3600 if PUBLIC_HTTPS else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "greenhouse",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth"]},
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "greengrow"),
        "USER": os.environ.get("DB_USER", "greengrow"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "db"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": ["greenhouse.permissions.CsrfSessionAuthentication", "greenhouse.permissions.ApiKeyAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["greenhouse.permissions.SignedIn"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"assist": "10/min"},
}

SPECTACULAR_SETTINGS = {
    "TITLE": "GreenGrow Smart Greenhouse API",
    "DESCRIPTION": (
        "REST interface of the GreenGrow demonstrator: simulated sensor generator, readings, actuator "
        "commands and the assist component. Telemetry and actuator control between devices run over MQTT "
        "(see docs/asyncapi.yaml). Sign in at /accounts/login/ (session cookie) or send the X-API-Key header. Reading needs any account; changing things needs the grower role."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "APPEND_COMPONENTS": {
        "securitySchemes": {"ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}, "SessionAuth": {"type": "apiKey", "in": "cookie", "name": "sessionid"}}
    },
    "SECURITY": [{"ApiKeyAuth": []}, {"SessionAuth": []}],
}

# --- Accounts ---
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/app/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 8 * 3600
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- GreenGrow runtime configuration ---
API_KEY = os.environ.get("API_KEY", "")
SIM_TOKEN = os.environ.get("SIM_TOKEN", "")
SIM_SPEED = float(os.environ.get("SIM_SPEED", "1"))
SAMPLE_INTERVAL_MIN = float(os.environ.get("SAMPLE_INTERVAL_MIN", "30"))
# Simulation start: by default the first hour of the weather data, so the weather and the clock line up.
SIM_START = datetime.fromisoformat(os.environ["SIM_START"]) if os.environ.get("SIM_START") else None
# Weather input: real hourly data cached by scripts/fetch_weather.py (winter = coldest fortnight of 2025/26 at
# Cranfield, summer = first half of June 2026). Set WEATHER_FILE to use another file.
WEATHER_SCENARIO = os.environ.get("WEATHER_SCENARIO", "winter")
WEATHER_FILE = os.environ.get("WEATHER_FILE") or str(BASE_DIR / "greenhouse" / "data" / f"cranfield_{WEATHER_SCENARIO}.json")
# Beyond the end of the cached weather: "wrap" repeats it from the start; "climatology" draws each new day from real days of the same
# time of year (the data's own monthly distribution), so a year of real weather can run on indefinitely.
FUTURE_WEATHER = os.environ.get("FUTURE_WEATHER", "wrap")
FUTURE_WEATHER_SEED = int(os.environ.get("FUTURE_WEATHER_SEED", "1"))
AUTO_CONTROL = os.environ.get("AUTO_CONTROL", "1") == "1"
ZONE = os.environ.get("ZONE", "zone1")
# Wall-clock seconds between samples, and the freshness limit derived from it.
SAMPLE_INTERVAL_REAL_S = SAMPLE_INTERVAL_MIN * 60 / SIM_SPEED
MAX_READING_AGE_S = L.STALE_AFTER_INTERVALS * SAMPLE_INTERVAL_REAL_S + 10

MQTT_USER = os.environ.get("MQTT_USER", "backend")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ASSIST_MODEL = os.environ.get("ASSIST_MODEL", "claude-sonnet-5-5")
ASSIST_MODE = os.environ.get("ASSIST_MODE", "auto")  # auto | claude | mock
