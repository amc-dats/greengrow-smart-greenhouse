"""Registration, sign-in and roles: viewer reads, grower controls, admin injects faults; the API key still works for machines."""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

PW = "a-long-test-password-9"
CMD = "/api/v1/actuators/pump/command/"


@pytest.fixture
def web(db):
    return APIClient(enforce_csrf_checks=False)


def make(username, role):
    from django.core.management import call_command
    call_command("set_role", username, role, "--create", "--password", PW)


def login(client, username):
    assert client.post("/accounts/login/", {"username": username, "password": PW}).status_code == 302
    return client


def test_register_creates_a_viewer_and_signs_in(web):
    r = web.post("/accounts/register/", {"username": "ana", "password1": PW, "password2": PW})
    assert r.status_code == 302 and r["Location"] == "/app/"
    u = get_user_model().objects.get(username="ana")
    assert not u.is_staff and not u.groups.exists()
    page = web.get("/app/")
    assert page.status_code == 200 and b"viewer" in page.content and b'data-testid="water"' not in page.content


def test_registration_enforces_password_rules(web):
    r = web.post("/accounts/register/", {"username": "bob", "password1": "short1", "password2": "short1"})
    assert r.status_code == 200 and not get_user_model().objects.filter(username="bob").exists()
    r = web.post("/accounts/register/", {"username": "bob", "password1": PW, "password2": "different-password-9"})
    assert r.status_code == 200 and not get_user_model().objects.filter(username="bob").exists()


def test_anonymous_is_sent_to_login_and_api_refuses(web):
    r = web.get("/app/")
    assert r.status_code == 302 and r["Location"].startswith("/accounts/login/")
    assert web.get("/api/v1/readings/latest/").status_code == 403
    assert web.get("/api/v1/health/").status_code == 200   # the health check stays open


def test_wrong_password_is_refused(web):
    make("cy", "viewer")
    r = web.post("/accounts/login/", {"username": "cy", "password": "nope"})
    assert r.status_code == 200 and b"Wrong username or password" in r.content


def test_viewer_can_read_but_not_command(web):
    make("vera", "viewer")
    login(web, "vera")
    assert web.get("/api/v1/actuators/").status_code == 200
    assert web.post(CMD, {"action": "stop"}, format="json").status_code == 403


def test_grower_can_command_but_not_inject_faults(web):
    make("gus", "grower")
    login(web, "gus")
    assert web.post(CMD, {"action": "stop"}, format="json").status_code == 202
    assert web.post("/api/v1/sim/fault/", {"sensor": "temperature", "mode": "stuck", "value": 20}, format="json").status_code == 403


def test_admin_can_use_the_fault_endpoint(web):
    make("ada", "admin")
    login(web, "ada")
    assert web.delete("/api/v1/sim/fault/").status_code == 204


def test_set_role_demotes_and_promotes(web):
    make("pat", "grower")
    make("pat", "viewer")
    login(web, "pat")
    assert web.post(CMD, {"action": "stop"}, format="json").status_code == 403


def test_session_writes_need_the_csrf_token(db):
    make("gus", "grower")
    c = APIClient(enforce_csrf_checks=True)
    c.post("/accounts/login/", {"username": "gus", "password": PW})   # login form itself is CSRF-protected, so this is refused
    assert c.post(CMD, {"action": "stop"}, format="json").status_code == 403


def test_api_key_still_authenticates_machines_but_a_wrong_key_does_not(web):
    from django.conf import settings
    assert web.post(CMD, {"action": "stop"}, format="json", HTTP_X_API_KEY=settings.API_KEY).status_code == 202
    assert web.post(CMD, {"action": "stop"}, format="json", HTTP_X_API_KEY="wrong").status_code == 403


def test_logout_ends_the_session(web):
    make("lou", "grower")
    login(web, "lou")
    assert web.post("/accounts/logout/").status_code == 302
    assert web.get("/api/v1/actuators/").status_code == 403


def test_admin_can_reset_a_password_and_it_signs_the_user_out(web):
    make("ada", "admin")
    make("tim", "grower")
    tim = APIClient()
    login(tim, "tim")
    assert tim.get("/api/v1/actuators/").status_code == 200
    login(web, "ada")
    uid = get_user_model().objects.get(username="tim").pk
    r = web.post("/app/users/", {"user_id": uid, "new_password": "brand-new-password-5"})
    assert r.status_code == 200 and b"Password reset for tim" in r.content
    assert tim.get("/api/v1/actuators/").status_code == 403                      # old session ended
    new = APIClient()
    assert new.post("/accounts/login/", {"username": "tim", "password": "brand-new-password-5"}).status_code == 302
    assert APIClient().post("/accounts/login/", {"username": "tim", "password": PW}).status_code == 200   # old password refused


def test_reset_enforces_password_rules(web):
    make("ada", "admin")
    make("tim", "grower")
    login(web, "ada")
    uid = get_user_model().objects.get(username="tim").pk
    r = web.post("/app/users/", {"user_id": uid, "new_password": "short"})
    assert b"reset-error" in r.content
    assert APIClient().post("/accounts/login/", {"username": "tim", "password": PW}).status_code == 302


def test_only_admins_reach_the_user_page(web):
    make("gus", "grower")
    assert web.get("/app/users/").status_code == 302          # not signed in
    login(web, "gus")
    assert web.get("/app/users/").status_code == 403
    assert web.post("/app/users/", {"user_id": 1, "new_password": "x"}).status_code == 403


def test_login_lockout_after_repeated_failures(web):
    from django.core.cache import cache
    cache.clear()
    make("lock", "viewer")
    for _ in range(5):
        assert web.post("/accounts/login/", {"username": "lock", "password": "wrong"}).status_code == 200
    assert web.post("/accounts/login/", {"username": "lock", "password": PW}).status_code == 429   # even the right password is refused
    cache.clear()
    assert web.post("/accounts/login/", {"username": "lock", "password": PW}).status_code == 302


def test_registration_is_rate_limited_per_address(web):
    from django.core.cache import cache
    cache.clear()
    for i in range(10):
        assert web.post("/accounts/register/", {"username": f"u{i}", "password1": PW, "password2": PW}).status_code == 302
        web.get("/accounts/logout/")
        web.post("/accounts/logout/")
    assert web.post("/accounts/register/", {"username": "u99", "password1": PW, "password2": PW}).status_code == 429
    cache.clear()


def test_admin_can_change_a_users_role_and_it_signs_them_out(web):
    make("ada", "admin")
    make("tim", "viewer")
    tim = APIClient()
    login(tim, "tim")
    assert tim.post(CMD, {"action": "stop"}, format="json").status_code == 403           # viewer
    login(web, "ada")
    uid = get_user_model().objects.get(username="tim").pk
    r = web.post("/app/users/", {"action": "role", "user_id": uid, "role": "grower"})
    assert r.status_code == 200 and b"tim is now grower" in r.content
    assert tim.get("/api/v1/actuators/").status_code == 403                               # old session ended
    tim2 = APIClient()
    login(tim2, "tim")
    assert tim2.post(CMD, {"action": "stop"}, format="json").status_code == 202           # now a grower
    web.post("/app/users/", {"action": "role", "user_id": uid, "role": "viewer"})
    tim3 = APIClient()
    login(tim3, "tim")
    assert tim3.post(CMD, {"action": "stop"}, format="json").status_code == 403           # demoted


def test_admin_cannot_change_their_own_role_or_use_unknown_roles(web):
    make("ada", "admin")
    make("tim", "grower")
    login(web, "ada")
    me = get_user_model().objects.get(username="ada").pk
    assert b"cannot change your own role" in web.post("/app/users/", {"action": "role", "user_id": me, "role": "viewer"}).content
    assert web.post("/api/v1/sim/fault/", {}, format="json").status_code != 403           # still an admin
    tim = get_user_model().objects.get(username="tim").pk
    assert b"Unknown role" in web.post("/app/users/", {"action": "role", "user_id": tim, "role": "superhero"}).content


def test_non_admins_cannot_change_roles(web):
    make("gus", "grower")
    make("tim", "viewer")
    login(web, "gus")
    uid = get_user_model().objects.get(username="tim").pk
    assert web.post("/app/users/", {"action": "role", "user_id": uid, "role": "admin"}).status_code == 403
    assert not get_user_model().objects.get(username="tim").is_staff


@pytest.mark.parametrize("role", ["viewer", "grower"])
def test_users_page_and_link_are_for_admins_only(web, role):
    make("pat", role)
    login(web, "pat")
    assert b'data-testid="users-link"' not in web.get("/app/").content        # no link in the console
    assert web.get("/app/users/").status_code == 403                           # and the page itself refuses
    assert web.post("/app/users/", {"action": "reset", "user_id": 1, "new_password": "brand-new-password-5"}).status_code == 403


def test_admin_sees_the_users_link(web):
    make("ada", "admin")
    login(web, "ada")
    assert b'data-testid="users-link"' in web.get("/app/").content


WRITE_ENDPOINTS = [
    ("post", "/api/v1/actuators/pump/command/", {"action": "stop"}),
    ("post", "/api/v1/feed/", {"duration_s": 5, "volume_ml": 1}),
    ("post", "/api/v1/assist/suggestions/", {}),
    ("post", "/api/v1/assist/suggestions/1/approve/", {}),
    ("post", "/api/v1/assist/suggestions/1/reject/", {}),
    ("post", "/api/v1/sim/fault/", {"sensor": "temperature", "mode": "stuck", "value": 20}),
    ("delete", "/api/v1/sim/fault/", None),
]


@pytest.mark.parametrize("method,path,body", WRITE_ENDPOINTS)
def test_every_write_endpoint_refuses_anonymous_and_viewers(web, method, path, body):
    call = lambda c: getattr(c, method)(path, body, format="json") if body is not None else getattr(c, method)(path)
    assert call(APIClient()).status_code == 403                    # not signed in
    make("vic", "viewer")
    login(web, "vic")
    assert call(web).status_code == 403                            # signed in as a viewer: still refused


def test_the_docs_page_is_only_documentation_and_gives_viewers_no_write_access(web):
    make("vic", "viewer")
    login(web, "vic")
    assert web.get("/api/docs/").status_code == 200                # the page itself is readable
    # "Try it out" in the docs sends the viewer's own session, so the same rules apply:
    assert web.post("/api/v1/actuators/pump/command/", {"action": "stop"}, format="json").status_code == 403


def test_dashboard_data_needs_a_sign_in_and_has_season_lines(web):
    from greenhouse import bridge
    from tests.test_flow import Recorder, telemetry
    assert APIClient().get("/api/v1/dashboard/").status_code == 403
    bridge.handle_telemetry(telemetry(), Recorder())
    bridge.handle_telemetry(telemetry(), Recorder())
    make("vic", "viewer")
    login(web, "vic")
    d = web.get("/api/v1/dashboard/?days=3").json()                         # a viewer may read it
    assert set(d) >= {"sim_now", "season", "series", "daily", "limits"}
    s = d["series"]
    assert len(s["t"]) == len(s["temperature"]) == len(s["safety_min"]) == 2
    assert d["season"] in ("rest", "growing")
    assert s["safety_min"][0] == (10.0 if d["season"] == "rest" else 15.0)
    page = web.get("/app/").content
    assert b'data-testid="chart-temperature"' in page and b'data-testid="chart-dli"' in page


def test_dashboard_carries_suggestions_for_the_period_and_the_console_hides_the_box_when_empty(web):
    from greenhouse import bridge
    from tests.test_flow import Recorder, telemetry
    bridge.handle_telemetry(telemetry(temperature=22.0, soil_moisture=35.0), Recorder())
    make("vic", "viewer")
    login(web, "vic")
    d = web.get("/api/v1/dashboard/?days=3").json()
    assert isinstance(d["suggestions"], list)
    page = web.get("/app/").content
    assert b'id="sugbox" hidden' in page and b"Nothing needs attention" not in page   # empty periods show nothing
