import os
import random
import pathlib

import pytest

SHOTS = pathlib.Path(os.environ.get("SHOTS_DIR", "/e2e/screenshots"))
SHOTS.mkdir(parents=True, exist_ok=True)
# every test run uses its own fake client address, so the sign-in lock-out test cannot lock out a real address
RUN_IP = f"198.51.100.{random.randint(2, 250)}"


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1280, "height": 900}, "extra_http_headers": {"CF-Connecting-IP": RUN_IP}}


@pytest.fixture
def shot():
    """shot(page, '01-name') saves a full-page screenshot to the screenshots folder."""
    def take(page, name):
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
    return take
