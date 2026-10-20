"""Kid UI: per-profile feature switches, reactions, navigation and pages."""

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.models import Profile, Video
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository
from app.template_utils import excerpt, friendly_date


def _setup(app, **flags):
    conn = next(app.dependency_overrides[get_db]())
    ch = ChannelRepository(conn).create("Trains")
    VideoRepository(conn).insert(Video(
        video_id="yt_train", title="Steam train", original_title="Steam train",
        channel_id=ch, status="active", download_status="ready"))
    pid = ProfileRepository(conn).create(Profile(name="sam", display_name="Sam", **flags))
    client = TestClient(app, follow_redirects=False)
    client.post("/profiles/select", data={"profile_id": pid})
    return conn, client, pid


def test_minimal_profile_hides_extra_features(app):
    _, kid, _ = _setup(app, allow_comments=False, allow_tags=False,
                       allow_customize=False, allow_upload=False)
    page = kid.get("/watch/yt_train").text
    assert 'id="comments"' not in page
    assert 'id="tags"' not in page
    assert "Customize" not in page
    assert 'data-reaction="love"' in page
    home = kid.get("/").text
    assert 'href="/upload"' not in home
    assert 'href="/tags"' not in home


def test_switched_off_features_are_refused_server_side(app):
    _, kid, _ = _setup(app, allow_comments=False, allow_tags=False,
                       allow_customize=False, allow_upload=False,
                       allow_reactions=False)
    assert kid.post("/watch/yt_train/comment", data={"body": "hi"}).status_code == 403
    assert kid.post("/video/yt_train/tags/add", data={"tag": "x"}).status_code == 403
    assert kid.get("/video/yt_train/edit").status_code == 302
    assert kid.get("/upload").status_code == 302
    assert kid.get("/tags").status_code == 302
    assert kid.post("/api/react", data={"video_id": "yt_train", "emoji": "love"}).status_code == 403


def test_new_profile_form_starts_simple(authed_client):
    import re
    page = authed_client.get("/parent/profiles/create").text
    checked = set(re.findall(r'name="(allow_\w+|auto_subtitles)" value="1"\s+checked', page))
    # Saving a video and hiding one the kid dislikes are on too.
    assert checked == {"allow_reactions", "allow_download", "allow_hide"}


def test_reaction_toggles_and_reports_counts(app):
    _, kid, _ = _setup(app)
    first = kid.post("/api/react", data={"video_id": "yt_train", "emoji": "love"})
    assert first.json() == {"mine": "love", "counts": {"love": 1}}
    second = kid.post("/api/react", data={"video_id": "yt_train", "emoji": "love"})
    assert second.json()["mine"] is None
    assert second.json()["counts"].get("love", 0) == 0


def test_home_marks_current_page_and_shows_reaction_emoji(app):
    _, kid, _ = _setup(app)
    kid.post("/api/react", data={"video_id": "yt_train", "emoji": "love"})
    home = kid.get("/").text
    assert 'aria-current="page"' in home
    assert "❤" in home
    assert '<html lang="en">' in home


def test_watch_page_blocks_cast_and_pip_and_download_when_off(app):
    _, kid, _ = _setup(app, allow_download=False)
    page = kid.get("/watch/yt_train").text
    assert 'controlsList="nodownload noremoteplayback"' in page
    assert "disablePictureInPicture" in page
    assert 'id="end-card"' in page


def test_watch_page_offers_to_carry_on_for_the_kid_who_stopped(app):
    _, kid, pid = _setup(app)
    page = kid.get("/watch/yt_train").text
    assert f'data-profile-id="{pid}"' in page
    assert 'id="resume-card"' in page and "Carry on" in page and "From the start" in page


def test_pin_page_has_keypad(app, client):
    conn = next(app.dependency_overrides[get_db]())
    from app.services.profiles import ProfileService
    pid = ProfileService(ProfileRepository(conn)).create(name="pat", display_name="Pat", pin="1234")
    ProfileService(ProfileRepository(conn)).create(name="max", display_name="Max")
    page = client.post("/profiles/select", data={"profile_id": pid}).text
    assert 'class="pin-pad"' in page
    assert page.count('class="pin-key"') == 10


def test_friendly_date():
    from datetime import date
    today = date(2026, 10, 8)
    assert friendly_date("2026-10-08 09:00:00", today) == "today"
    assert friendly_date("2026-10-07", today) == "yesterday"
    assert friendly_date("2026-10-05", today) == "3 days ago"
    assert friendly_date("2026-09-01", today) == "1 Sep 2026"
    assert friendly_date("", today) == ""


def test_long_descriptions_end_at_a_sentence_or_a_word():
    text = "Koro the llama is hungry. " * 10 + "The penguin wants the last berry too"
    short = excerpt(text, 100)
    assert short.endswith("hungry.") and len(short) <= 100
    words = "llama " * 40
    assert excerpt(words, 50) == ("llama " * 8).rstrip() + "…"
    assert excerpt("Short.", 500) == "Short."
    assert excerpt(None, 500) == ""


def test_kid_upload_xhr_gets_json(app):
    from unittest.mock import patch
    from app.services.media_probe import ProbeResult
    conn, kid, pid = _setup(app)
    mine = ChannelRepository(conn).create("Sam's", owner_profile_id=pid)
    with patch("app.services.uploads.MediaProbeService.validate",
               return_value=ProbeResult(codec_name="h264", width=320, height=240,
                                        duration_seconds=1.0, container="mov")), \
         patch("app.services.uploads.ThumbnailService.extract_frame", return_value=None):
        resp = kid.post("/upload", data={"channel_id": str(mine)},
                        files={"file": ("a.mp4", b"a" * 100, "video/mp4")},
                        headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200
    assert resp.json()["redirect"].startswith("/watch/up_")

    app.state.config.storage.max_kid_upload_bytes = 10
    resp = kid.post("/upload", data={"channel_id": str(mine)},
                    files={"file": ("b.mp4", b"b" * 100, "video/mp4")},
                    headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code in (413,)


def test_bookmarked_videos_show_in_kids_own_channel(app):
    conn, kid, pid = _setup(app)
    mine = ChannelRepository(conn).create("Sam's faves", owner_profile_id=pid)
    from app.repositories.profile_channel_video_repo import ProfileChannelVideoRepository
    ProfileChannelVideoRepository(conn).add(pid, mine, "yt_train")
    page = kid.get(f"/channel/{mine}").text
    assert "Steam train" in page


def test_pages_declare_the_app_icon(client):
    for path in ("/profiles", "/parent/login"):
        assert 'href="/static/icons/favicon.ico"' in client.get(path).text
    icon = client.get("/static/icons/favicon.ico")
    assert icon.status_code == 200 and icon.content[:4] == b"\x00\x00\x01\x00"



@pytest.mark.parametrize("allowed", [True, False])
def test_help_lists_only_what_the_kid_can_do(app, allowed):
    _, kid, _ = _setup(app, allow_tags=allowed, allow_wishlist=allowed)
    page = kid.get("/help").text
    assert "Carry on" in page
    assert ("Put a word on a video" in page) is allowed
    assert ("Ask for a new video" in page) is allowed


def test_one_channel_needs_no_channel_buttons(app):
    conn, kid, _ = _setup(app)
    assert 'class="channel-nav"' not in kid.get("/").text
    planes = ChannelRepository(conn).create("Planes")
    conn.commit()
    # An empty channel is not offered either.
    assert 'class="channel-nav"' not in kid.get("/").text
    VideoRepository(conn).insert(Video(
        video_id="yt_plane", title="Jumbo jet", original_title="Jumbo jet",
        channel_id=planes, status="active", download_status="ready"))
    assert 'class="channel-nav"' in kid.get("/").text


def test_label_emoji_ask_for_colour():
    # U+1F3F7 draws as a plain outline without the emoji selector.
    import re
    from pathlib import Path
    for path in (Path(__file__).parent.parent / "app" / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"&#127991;(?!&#65039;)", text), path
        assert "\U0001F3F7" not in text.replace("\U0001F3F7️", ""), path


def test_reaction_labels_capitalise_only_the_first_word(app):
    conn, kid, pid = _setup(app)
    ProfileRepository(conn).update(pid, language="es")
    page = kid.get("/watch/yt_train").text
    assert ">Me encanta<" in page and "text-transform: capitalize" not in kid.get("/static/kid/style.css").text


def test_video_lengths_read_like_the_player():
    from app.template_utils import clock_duration
    assert clock_duration(83) == "1:23"
    assert clock_duration(18785) == "5:13:05"
    assert clock_duration(0) == "" and clock_duration(None) == ""
