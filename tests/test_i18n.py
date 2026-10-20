"""Kid screens in other languages."""
import json
import re
import string
from pathlib import Path

import pytest

from app.constants import PIN_PICTURES
from app.i18n import (
    DEFAULT_LANGUAGE, JS_MESSAGES, LANGUAGE_CODES, SERVER_MESSAGES, catalog, gettext,
)
from app.repositories import ProfileRepository
from app.services.profiles import ProfileService
from app.services.reactions import EMOJI_MAP

KID_TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates" / "base" / "kid"
_CALL = re.compile(r"""_\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")


def kid_strings() -> set[str]:
    found: set[str] = set()
    for tpl in KID_TEMPLATES.glob("*.html"):
        for dq, sq in _CALL.findall(tpl.read_text(encoding="utf-8")):
            found.add(dq or sq)
    found.update(JS_MESSAGES)
    found.update(SERVER_MESSAGES)
    found.update(name for name, _emoji in EMOJI_MAP)
    found.update(name for _digit, _emoji, name in PIN_PICTURES)
    return found


OTHER_LANGUAGES = [c for c in LANGUAGE_CODES if c != DEFAULT_LANGUAGE]


def _fields(text):
    return {f for _lit, f, _spec, _conv in string.Formatter().parse(text) if f}


@pytest.mark.parametrize("lang", OTHER_LANGUAGES)
def test_catalog_translates_every_kid_string(lang):
    cat = catalog(lang)
    needed = kid_strings()
    assert sorted(needed - set(cat)) == []
    assert sorted(set(cat) - needed) == [], "unused catalog entries"
    for english, translated in cat.items():
        assert translated.strip(), english
        assert _fields(translated) == _fields(english), english


def test_unknown_language_falls_back_to_english():
    assert gettext("xx", "Home") == "Home"
    assert gettext(None, "{n} days ago", n=3) == "3 days ago"
    assert gettext("pt-BR", "{n} days ago", n=3) == "há 3 dias"


def _kid(app, client, lang, pin=""):
    db = next(iter(app.dependency_overrides.values()))()
    conn = next(db)
    svc = ProfileService(ProfileRepository(conn))
    pid = svc.create(name=f"k{lang}", display_name="Kid", language=lang, pin=pin)
    return pid


def test_kid_screens_follow_the_profile_language(app, client):
    pid = _kid(app, client, "pt-BR")
    client.post("/profiles/select", data={"profile_id": pid})
    home = client.get("/").text
    assert '<html lang="pt-BR">' in home
    assert "Início" in home and ">Home<" not in home
    messages = json.loads(re.search(r'data-messages="([^"]*)"', home).group(1)
                          .replace("&#34;", '"').replace("&quot;", '"'))
    assert messages["Sending..."] == "Enviando..."
    help_page = client.get("/help").text
    assert "Como funciona" in help_page


def test_pin_page_and_errors_use_the_profile_language(app, client):
    pid = _kid(app, client, "es", pin="1234")
    page = client.post("/profiles/select", data={"profile_id": pid}, follow_redirects=True).text
    assert '<html lang="es">' in page and "Volver" in page
    wrong = client.post("/profiles/pin", data={"profile_id": pid, "pin": "9999"}, follow_redirects=True).text
    assert "Clave incorrecta" in wrong


def test_picker_uses_the_server_default_language(app, client):
    app.state.config.server.kid_language = "pt-BR"
    try:
        assert "Quem vai assistir?" in client.get("/profiles").text
    finally:
        app.state.config.server.kid_language = "en"


def test_parent_sets_kid_language(app, authed_client):
    resp = authed_client.post("/parent/profiles/create", data={
        "display_name": "Lia", "language": "es"})
    assert resp.status_code == 302
    form = authed_client.get("/parent/profiles/create").text
    assert 'value="pt-BR"' in form


def test_the_kids_error_page_is_rendered_in_the_kids_language(app, client, monkeypatch):
    from app.dependencies import get_db
    page = client.get("/no-such-page")
    assert page.status_code == 404
    assert "{%" not in page.text and "{{" not in page.text
    assert "Oops! That didn" in page.text
    conn = next(app.dependency_overrides[get_db]())

    class Shared:
        def __getattr__(self, name):
            return getattr(conn, name)

        def close(self):
            pass
    monkeypatch.setattr(app.state, "db_connect", Shared)
    pid = ProfileService(ProfileRepository(conn)).create(name="lu", display_name="Lu", language="es")
    conn.commit()
    client.post("/profiles/select", data={"profile_id": pid})
    page = client.get("/no-such-page")
    assert "{%" not in page.text and 'lang="es"' in page.text


@pytest.mark.parametrize("lang", LANGUAGE_CODES)
def test_kid_menu_labels_are_all_different(lang):
    labels = ["Home", "Search", "Tags", "Find new", "Upload", "Help", "Switch"]
    shown = [gettext(lang, label) for label in labels]
    assert len(set(shown)) == len(shown), shown
