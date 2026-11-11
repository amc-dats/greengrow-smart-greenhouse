"""Browser tests (Playwright) for registration, sign-in and roles against the running stack.

Setup (once; the grower account is created by an administrator, never by self-registration):
  docker compose exec api python manage.py set_role e2e_grower grower --create --password "$E2E_PASSWORD"
Run:
  docker run --rm --add-host=host.docker.internal:host-gateway -e BASE_URL=http://host.docker.internal:8000 -e E2E_PASSWORD=... \
      -v "$PWD/e2e:/e2e" -w /e2e mcr.microsoft.com/playwright/python:v1.49.0-jammy \
      sh -c "pip install -q pytest-playwright==0.6.2 && pytest -q"
(ALLOWED_HOSTS must include host.docker.internal: set ALLOWED_HOSTS in .env.)
"""
import os
import uuid

import pytest
from playwright.sync_api import Page, expect

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
PW = os.environ.get("E2E_PASSWORD", "")


def sign_in(page: Page, user: str):
    page.goto(f"{BASE}/accounts/login/")
    page.fill("#id_username", user)
    page.fill("#id_password", PW)
    page.click("button[type=submit]")


def test_anonymous_visit_redirects_to_login(page: Page):
    page.goto(f"{BASE}/app/")
    expect(page).to_have_url(f"{BASE}/accounts/login/?next=/app/")


def test_register_then_see_view_only_console(page: Page):
    name = f"viewer_{uuid.uuid4().hex[:8]}"
    page.goto(f"{BASE}/accounts/register/")
    page.fill("#id_username", name)
    page.fill("#id_password1", "Playwright-Pass-42")
    page.fill("#id_password2", "Playwright-Pass-42")
    page.click("button[type=submit]")
    expect(page).to_have_url(f"{BASE}/app/")
    expect(page.get_by_test_id("role")).to_have_text("viewer")
    expect(page.get_by_test_id("whoami")).to_have_text(name)
    expect(page.get_by_test_id("viewer-note")).to_be_visible()
    expect(page.get_by_test_id("water")).to_have_count(0)


def test_weak_password_is_rejected(page: Page):
    page.goto(f"{BASE}/accounts/register/")
    page.fill("#id_username", f"weak_{uuid.uuid4().hex[:6]}")
    page.fill("#id_password1", "12345678")
    page.fill("#id_password2", "12345678")
    page.click("button[type=submit]")
    expect(page).to_have_url(f"{BASE}/accounts/register/")
    expect(page.locator(".errorlist").first).to_be_visible()


def test_wrong_password_shows_an_error(page: Page):
    page.goto(f"{BASE}/accounts/login/")
    page.fill("#id_username", "nobody")
    page.fill("#id_password", "wrong")
    page.click("button[type=submit]")
    expect(page.get_by_test_id("login-error")).to_be_visible()


@pytest.mark.skipif(not PW, reason="set E2E_PASSWORD and create e2e_grower first")
def test_grower_can_water_and_sign_out(page: Page):
    sign_in(page, "e2e_grower")
    expect(page.get_by_test_id("role")).to_have_text("grower")
    page.get_by_test_id("water").click()
    expect(page.get_by_test_id("message")).to_have_text("Watering queued")
    page.get_by_test_id("logout").click()
    expect(page).to_have_url(f"{BASE}/accounts/login/")
    page.goto(f"{BASE}/app/")
    expect(page).to_have_url(f"{BASE}/accounts/login/?next=/app/")
