"""Settings for unit tests: in-memory SQLite, mock assist, fixed secrets. Never touches the real database or broker."""
from .settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
API_KEY = "test-api-key"
SIM_TOKEN = "test-sim-token"
ASSIST_MODE = "mock"
ANTHROPIC_API_KEY = ""
AUTO_CONTROL = True
