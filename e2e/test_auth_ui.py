"""Browser tests (Playwright) for registration, sign-in, roles and the admin Users page, against the running stack.

Setup (accounts above viewer are made by an administrator, never by self-registration):
  docker compose exec api python manage.py set_role e2e_grower grower --create --password "$E2E_PASSWORD"
  docker compose exec api python manage.py set_role e2e_admin  admin  --create --password "$E2E_PASSWORD"
Run on the compose network (the app must run with PUBLIC_HTTPS=0, otherwise the browser will not keep secure cookies over http):
  docker run --rm --network greengrow_default -e BASE_URL=http://api:8000 -e E2E_PASSWORD=... \
      -v "$PWD/e2e:/e2e" -w /e2e mcr.microsoft.com/playwright/python:v1.49.0-jammy \
      sh -c "pip install -q pytest-playwright==0.6.2 && pytest -q -p no:cacheprovider"
Screenshots of each step are written to e2e/screenshots/. These tests have NOT been run yet: the Playwright image is about 1.5 GB, which was too heavy for the laptop used so far.
"""
import os
import uuid

import pytest
from playwright.sync_api import Page, Browser, expect

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
PW = os.environ.get("E2E_PASSWORD", "")
STRONG = "Playwright-Pass-42x"
needs_accounts = pytest.mark.skipif(not PW, reason="set E2E_PASSWORD and create e2e_grower / e2e_admin first")


def sign_in(page: Page, user: str, password: str = PW):
    page.goto(f"{BASE}/accounts/login/")
    page.fill("#id_username", user)
    page.fill("#id_password", password)
    page.click("button[type=submit]")


def register(page: Page, name: str):
    page.goto(f"{BASE}/accounts/register/")
    page.fill("#id_username", name)
    page.fill("#id_password1", STRONG)
    page.fill("#id_password2", STRONG)
    page.click("button[type=submit]")


# ------------------------------------------------------------------------------ anonymous
def test_anonymous_visit_redirects_to_login(page: Page, shot):
    page.goto(f"{BASE}/app/")
    expect(page).to_have_url(f"{BASE}/accounts/login/?next=/app/")
    expect(page.get_by_test_id("login-form")).to_be_visible()
    shot(page, "01-login-page")


def test_anonymous_api_is_refused_but_docs_and_health_are_readable(page: Page):
    assert page.request.get(f"{BASE}/api/v1/readings/latest/").status == 403
    assert page.request.get(f"{BASE}/api/v1/health/").status == 200
    assert page.request.get(f"{BASE}/api/docs/").status == 200
    assert page.request.post(f"{BASE}/api/v1/actuators/pump/command/", data={"action": "stop"}).status == 403


def test_wrong_password_shows_an_error(page: Page, shot):
    sign_in(page, "nobody", "wrong")
    expect(page.get_by_test_id("login-error")).to_be_visible()
    shot(page, "02-wrong-password")


def test_weak_password_is_rejected(page: Page, shot):
    page.goto(f"{BASE}/accounts/register/")
    page.fill("#id_username", f"weak_{uuid.uuid4().hex[:6]}")
    page.fill("#id_password1", "12345678")
    page.fill("#id_password2", "12345678")
    shot(page, "03-register-form")
    page.click("button[type=submit]")
    expect(page).to_have_url(f"{BASE}/accounts/register/")
    expect(page.locator(".errorlist").first).to_be_visible()
    shot(page, "04-weak-password-rejected")


def test_sign_in_is_locked_after_five_failures(browser: Browser, shot):
    ctx = browser.new_context(extra_http_headers={"CF-Connecting-IP": f"203.0.113.{uuid.uuid4().int % 200 + 2}"})
    page = ctx.new_page()
    for _ in range(5):
        sign_in(page, "nobody", "wrong")
    sign_in(page, "nobody", "wrong")
    expect(page.locator("body")).to_contain_text("Too many failed sign-ins")
    shot(page, "05-lockout")
    ctx.close()


# ------------------------------------------------------------------------------ viewer
def test_register_then_see_view_only_console(page: Page, shot):
    name = f"viewer_{uuid.uuid4().hex[:8]}"
    register(page, name)
    expect(page).to_have_url(f"{BASE}/app/")
    expect(page.get_by_test_id("role")).to_have_text("viewer")
    expect(page.get_by_test_id("whoami")).to_have_text(name)
    expect(page.get_by_test_id("viewer-note")).to_be_visible()
    expect(page.get_by_test_id("water")).to_have_count(0)
    expect(page.get_by_test_id("users-link")).to_have_count(0)
    expect(page.get_by_test_id("chart-temperature")).to_be_visible()
    page.wait_for_timeout(2500)
    shot(page, "06-viewer-console")


def test_viewer_cannot_open_the_users_page_or_write_through_the_api(page: Page, shot):
    register(page, f"viewer_{uuid.uuid4().hex[:8]}")
    resp = page.goto(f"{BASE}/app/users/")
    assert resp.status == 403
    expect(page.locator("body")).to_contain_text("administrators only")
    shot(page, "07-viewer-users-page-forbidden")
    csrf = next(c["value"] for c in page.context.cookies() if c["name"] == "csrftoken")
    r = page.request.post(f"{BASE}/api/v1/actuators/pump/command/", data={"action": "stop"}, headers={"X-CSRFToken": csrf})
    assert r.status == 403
    assert page.request.get(f"{BASE}/api/v1/readings/latest/").status in (200, 404)    # reading is allowed


# ------------------------------------------------------------------------------ grower
@needs_accounts
def test_grower_can_water_but_has_no_users_page(page: Page, shot):
    sign_in(page, "e2e_grower")
    expect(page.get_by_test_id("role")).to_have_text("grower")
    expect(page.get_by_test_id("users-link")).to_have_count(0)
    page.get_by_test_id("water").click()
    expect(page.get_by_test_id("message")).to_have_text("Watering queued")
    shot(page, "08-grower-console")
    assert page.goto(f"{BASE}/app/users/").status == 403
    page.goto(f"{BASE}/app/")
    page.get_by_test_id("logout").click()
    expect(page).to_have_url(f"{BASE}/accounts/login/")
    page.goto(f"{BASE}/app/")
    expect(page).to_have_url(f"{BASE}/accounts/login/?next=/app/")
    shot(page, "09-after-sign-out")


# ------------------------------------------------------------------------------ admin
@needs_accounts
def test_admin_changes_a_role_and_resets_a_password(page: Page, browser: Browser, shot):
    victim = f"newbie_{uuid.uuid4().hex[:6]}"
    other = browser.new_context(extra_http_headers={"CF-Connecting-IP": f"192.0.2.{uuid.uuid4().int % 200 + 2}"})
    vp = other.new_page()
    register(vp, victim)                                    # a new viewer signs up
    expect(vp.get_by_test_id("role")).to_have_text("viewer")

    sign_in(page, "e2e_admin")
    expect(page.get_by_test_id("role")).to_have_text("admin")
    page.get_by_test_id("users-link").click()
    expect(page.get_by_test_id("users")).to_be_visible()
    expect(page.get_by_test_id(f"role-{victim}")).to_have_text("viewer")
    shot(page, "10-admin-users-page")

    page.locator(f"tr:has-text('{victim}') select").select_option("grower")      # promote the new viewer
    page.get_by_test_id(f"role-btn-{victim}").click()
    expect(page.get_by_test_id(f"role-{victim}")).to_have_text("grower")
    expect(page.locator("body")).to_contain_text(f"{victim} is now grower")
    shot(page, "11-admin-changed-role")

    vp.goto(f"{BASE}/app/")                                 # the promoted user's old session was ended
    expect(vp).to_have_url(f"{BASE}/accounts/login/?next=/app/")
    sign_in(vp, victim, STRONG)
    expect(vp.get_by_test_id("role")).to_have_text("grower")
    expect(vp.get_by_test_id("water")).to_be_visible()
    shot(vp, "12-promoted-user-is-grower")

    new_pw = "Reset-By-Admin-77z"
    page.locator(f"tr:has-text('{victim}') input[name=new_password]").fill(new_pw)
    page.get_by_test_id(f"reset-{victim}").click()
    expect(page.get_by_test_id("reset-ok")).to_contain_text(f"Password reset for {victim}")
    shot(page, "13-admin-reset-password")
    vp.goto(f"{BASE}/app/")
    expect(vp).to_have_url(f"{BASE}/accounts/login/?next=/app/")                  # signed out by the reset
    sign_in(vp, victim, STRONG)
    expect(vp.get_by_test_id("login-error")).to_be_visible()                      # the old password no longer works
    sign_in(vp, victim, new_pw)
    expect(vp).to_have_url(f"{BASE}/app/")

    page.locator("tr:has-text('e2e_admin') select").is_disabled()                 # an admin cannot change their own role
    assert page.locator("tr:has-text('e2e_admin') select").is_disabled()
    shot(page, "14-admin-cannot-change-own-role")
    other.close()
