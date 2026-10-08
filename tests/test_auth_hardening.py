"""Brute-force limits, PIN hashing and session invalidation."""

from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.repositories import ProfileRepository
from app.services.auth import hash_pin, verify_pin
from app.services.profiles import ProfileService


def _svc(app):
    return ProfileService(ProfileRepository(next(app.dependency_overrides[get_db]())))


def test_pin_hash_roundtrip_and_legacy_plaintext():
    stored = hash_pin("1234")
    assert stored != "1234"
    assert verify_pin(stored, "1234")
    assert not verify_pin(stored, "4321")
    assert verify_pin("1234", "1234")
    assert not verify_pin("", "")


def test_pin_attempts_are_throttled(app, client):
    svc = _svc(app)
    pid = svc.create(name="ann", display_name="Ann", pin="1111")
    svc.create(name="bo", display_name="Bo")
    for _ in range(5):
        resp = client.post("/profiles/pin", data={"profile_id": pid, "pin": "0000"})
        assert "Wrong PIN" in resp.text
    resp = client.post("/profiles/pin", data={"profile_id": pid, "pin": "1111"})
    assert resp.status_code == 200
    assert "Too many tries" in resp.text


def test_correct_pin_enters_profile(app, client):
    svc = _svc(app)
    pid = svc.create(name="cy", display_name="Cy", pin="2222")
    resp = client.post("/profiles/pin", data={"profile_id": pid, "pin": "2222"})
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


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
