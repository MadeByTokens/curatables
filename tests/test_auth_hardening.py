"""Brute-force limits, PIN hashing and session invalidation."""

from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.repositories import ProfileRepository
from app.services.auth import hash_pin, verify_pin
from app.services.profiles import ProfileService


def _svc(app):
    return ProfileService(ProfileRepository(next(app.dependency_overrides[get_db]())))


def test_pin_hash_roundtrip():
    stored = hash_pin("1234")
    assert stored != "1234"
    assert verify_pin(stored, "1234")
    assert not verify_pin(stored, "4321")
    assert not verify_pin("1234", "1234")
    assert not verify_pin("", "")


def test_pin_attempts_are_throttled(app, client):
    svc = _svc(app)
    pid = svc.create(name="ann", display_name="Ann", pin="1111")
    svc.create(name="bo", display_name="Bo")
    for _ in range(5):
        resp = client.post("/profiles/pin", data={"profile_id": pid, "pin": "0000"}, follow_redirects=True)
        assert "Wrong PIN" in resp.text
    resp = client.post("/profiles/pin", data={"profile_id": pid, "pin": "1111"}, follow_redirects=True)
    assert resp.status_code == 200
    assert "Too many tries" in resp.text


def test_a_picture_pin_gets_more_tries_than_a_number_pin(app, client):
    from app.services.auth import PICTURE_PIN_LIMITER, PIN_LIMITER, pin_limits
    assert PICTURE_PIN_LIMITER.max_events > PIN_LIMITER.max_events
    assert pin_limits("pictures")[1] > pin_limits("digits")[1]
    pid = _svc(app).create(name="di", display_name="Di", pin="512", pin_style="pictures")
    for _ in range(PICTURE_PIN_LIMITER.max_events):
        resp = client.post("/profiles/pin", data={"profile_id": pid, "pin": "999"}, follow_redirects=True)
        assert "Wrong PIN" in resp.text
    resp = client.post("/profiles/pin", data={"profile_id": pid, "pin": "512"}, follow_redirects=True)
    assert "Too many tries" in resp.text


def test_correct_pin_enters_profile(app, client):
    svc = _svc(app)
    pid = svc.create(name="cy", display_name="Cy", pin="2222")
    resp = client.post("/profiles/pin", data={"profile_id": pid, "pin": "2222"})
    assert resp.status_code == 302
    assert resp.headers["location"] == "/profiles?signed_in=1"
    assert client.get(resp.headers["location"]).headers["location"] == "/"


def test_a_browser_that_drops_the_cookie_is_told_instead_of_bounced(app, client):
    # A private tab that blocks cookies: the PIN is right, but the next
    # request arrives without the session.
    pid = _svc(app).create(name="dee", display_name="Dee", pin="3333")
    resp = client.post("/profiles/pin", data={"profile_id": pid, "pin": "3333"})
    client.cookies.clear()
    page = client.get(resp.headers["location"])
    assert page.status_code == 200
    assert "keep the sign-in" in page.text


def test_the_only_kid_without_a_pin_does_not_loop_without_cookies(app, client):
    _svc(app).create(name="solo", display_name="Solo")
    resp = client.get("/profiles")
    assert resp.headers["location"] == "/profiles?signed_in=1"
    client.cookies.clear()
    page = client.get(resp.headers["location"])
    assert page.status_code == 200
    assert "keep the sign-in" in page.text
    # With the cookie kept, the same step goes on to the kid's home.
    fresh = TestClient(app, follow_redirects=False)
    first = fresh.get("/profiles")
    assert fresh.get(first.headers["location"]).headers["location"] == "/"


def test_parent_sign_in_explains_a_dropped_cookie(client):
    resp = client.post("/parent/login", data={"password": "testpass"})
    assert resp.headers["location"] == "/parent/login?signed_in=1"
    assert client.get(resp.headers["location"]).headers["location"] == "/parent/"
    client.cookies.clear()
    page = client.get(resp.headers["location"])
    assert page.status_code == 200
    assert "keep the sign-in" in page.text


def test_parent_login_is_throttled(client):
    for _ in range(10):
        client.post("/parent/login", data={"password": "wrong"})
    resp = client.post("/parent/login", data={"password": "testpass"})
    assert resp.status_code == 429


def test_password_change_signs_out_other_sessions(app, authed_client):
    other = TestClient(app, follow_redirects=False)
    assert other.post("/parent/login", data={"password": "testpass"}).status_code == 302
    assert other.get("/parent/").status_code == 200

    resp = authed_client.post("/parent/settings/password", data={
        "current_password": "testpass",
        "new_password": "a-much-longer-one",
        "new_password2": "a-much-longer-one",
    })
    assert "signed out" in resp.text
    assert authed_client.get("/parent/").status_code == 200
    resp = other.get("/parent/")
    assert resp.status_code == 302
    assert "/parent/login" in resp.headers["location"]


def test_migration_hashes_plaintext_pins(db):
    import importlib.util
    from pathlib import Path
    db.execute("INSERT INTO profiles (name, pin) VALUES ('old', '9876')")
    path = Path("app/db/migrations/0005_hash_profile_pins.py")
    spec = importlib.util.spec_from_file_location("m0005", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.up(db)
    mod.up(db)
    stored = db.execute("SELECT pin FROM profiles WHERE name='old'").fetchone()["pin"]
    assert stored.startswith("pbkdf2$")
    assert verify_pin(stored, "9876")


def test_config_json_is_owner_only(app):
    import stat
    app.state.config.save()
    mode = stat.S_IMODE(app.state.config.config_path().stat().st_mode)
    assert mode == 0o600


def test_pointer_to_missing_library_refuses_to_start(tmp_path, monkeypatch):
    import json
    import pytest
    from app.config import load_config
    home = tmp_path / "home"
    (home / "curatables-data").mkdir(parents=True)
    (home / "curatables-data" / "config.json").write_text(
        json.dumps({"storage": {"path": str(tmp_path / "unplugged")}}))
    monkeypatch.setattr("app.config.Path.home", lambda: home)
    with pytest.raises(RuntimeError, match="not there"):
        load_config()


def test_login_throttle_survives_restart_and_caps_all_clients(db):
    from app.repositories import LoginFailureRepository
    from app.services.auth import (
        LOGIN_FAILURES_PER_CLIENT, LOGIN_FAILURES_TOTAL, LOGIN_WINDOW_SECONDS, LoginThrottle,
    )
    throttle = LoginThrottle(LoginFailureRepository(db))
    for _ in range(LOGIN_FAILURES_PER_CLIENT):
        throttle.failed("10.0.0.2")
    restarted = LoginThrottle(LoginFailureRepository(db))
    assert restarted.blocked("10.0.0.2")
    assert not restarted.blocked("10.0.0.3")

    for i in range(LOGIN_FAILURES_TOTAL):
        restarted.failed(f"10.0.1.{i}")
    assert restarted.blocked("10.0.0.3")

    import time
    assert not restarted.blocked("10.0.0.2", now=time.time() + LOGIN_WINDOW_SECONDS + 1)


def test_successful_login_clears_that_clients_failures(client):
    for _ in range(9):
        client.post("/parent/login", data={"password": "wrong"})
    assert client.post("/parent/login", data={"password": "testpass"}).status_code == 302
    for _ in range(9):
        client.post("/parent/login", data={"password": "wrong"})
    assert client.post("/parent/login", data={"password": "testpass"}).status_code == 302


def test_picture_pin_profile_round_trip(app, authed_client, client):
    resp = authed_client.post("/parent/profiles/create", data={
        "display_name": "Pia", "pin_style": "pictures",
        "pin_pic_1": "4", "pin_pic_2": "1", "pin_pic_3": "9",
    })
    assert resp.status_code == 302
    profile = [p for p in _svc(app).list() if p.display_name == "Pia"][0]
    assert profile.pin_style == "pictures"
    assert profile.pin.startswith("pbkdf2$")

    page = client.post("/profiles/select", data={"profile_id": profile.id}, follow_redirects=True)
    assert 'data-auto-submit="1"' in page.text
    assert page.text.count('class="pin-key pin-key-picture"') == 9
    assert client.post("/profiles/pin", data={
        "profile_id": profile.id, "pin": "419"}).status_code == 302


def test_picture_pin_needs_all_three(authed_client):
    resp = authed_client.post("/parent/profiles/create", data={
        "display_name": "Half", "pin_style": "pictures", "pin_pic_1": "4"})
    assert resp.status_code == 400
    assert "Pick all 3 pictures" in resp.text


def test_changing_pin_kind_requires_a_new_pin(app, authed_client):
    pid = _svc(app).create(name="kai", display_name="Kai", pin="1234")
    resp = authed_client.post(f"/parent/profiles/{pid}/edit", data={
        "display_name": "Kai", "pin_style": "pictures"})
    assert resp.status_code == 400
    assert "changed the kind of PIN" in resp.text
    resp = authed_client.post(f"/parent/profiles/{pid}/edit", data={
        "display_name": "Kai", "pin_style": "pictures",
        "pin_pic_1": "2", "pin_pic_2": "2", "pin_pic_3": "3"})
    assert resp.status_code == 302
    kai = _svc(app).get(pid)
    assert kai.pin_style == "pictures"
    assert _svc(app).check_pin(kai, "223")


def test_pin_guessing_from_many_addresses_is_capped_per_profile(db):
    from app.repositories.login_failure_repo import LoginFailureRepository
    from app.services.auth import PIN_FAILURES_PER_PROFILE, LoginThrottle, PinThrottle
    repo = LoginFailureRepository(db)
    pins = PinThrottle(repo)
    for _ in range(PIN_FAILURES_PER_PROFILE):
        assert not pins.blocked(7)
        pins.failed(7)
    assert pins.blocked(7) and not pins.blocked(8)
    assert not LoginThrottle(repo).blocked("192.0.2.9"), "kids' wrong PINs never lock the parent out"
    pins.succeeded(7)
    assert not pins.blocked(7)


def test_a_parent_sign_in_ends_after_the_timeout_even_when_used(config, monkeypatch):
    import time as real_time

    from app.services import auth as auth_mod
    svc = auth_mod.AuthService(config)
    session = {}
    svc.start_parent_session(session)
    assert svc.is_parent_session(session)
    later = real_time.time() + config.parent.session_timeout_hours * 3600 + 1
    monkeypatch.setattr(auth_mod.time, "time", lambda: later)
    assert not svc.is_parent_session(session)


def test_sessions_from_before_the_sign_in_time_was_kept_must_sign_in_again(config):
    from app.services.auth import AuthService
    assert not AuthService(config).is_parent_session({"parent_authenticated": True, "parent_epoch": 0})
