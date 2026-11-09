import os

# settings.py needs a secret key at import time; the test settings override everything else.
os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")
