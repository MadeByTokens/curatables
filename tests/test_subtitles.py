"""Subtitles play in the browser whatever format they were stored in,
for uploaded videos as well as downloaded ones."""
from app.dependencies import get_db
from app.models import Video
from app.repositories import VideoRepository
from app.services.subtitles import (
    decode_text, language_code, language_label, srt_to_vtt, to_vtt,
)

SRT = (
    "1\r\n"
    "00:00:01,500 --> 00:00:03,000\r\n"
    "{\\an8}<font color=\"#fff\">Hello</font> <i>there</i>\r\n"
    "\r\n"
    "2\r\n"
    "0:00:04,25 --> 0:00:05,000\r\n"
    "Tom & Jerry <3\r\n"
)


def test_srt_becomes_webvtt():
    vtt = srt_to_vtt(SRT)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:01.500 --> 00:00:03.000\nHello <i>there</i>\n" in vtt
    assert "00:00:04.250 --> 00:00:05.000\nTom &amp; Jerry &lt;3\n" in vtt
    assert "font" not in vtt and "\\an8" not in vtt
    assert "\n1\n" not in vtt


def test_old_windows_encoded_srt_is_readable():
    data = "1\n00:00:01,000 --> 00:00:02,000\nOlá, ação!\n".encode("cp1252")
    assert "Olá, ação!" in to_vtt(data, ".srt")
    assert decode_text(b"\xef\xbb\xbfWEBVTT\n") == "WEBVTT\n"
    assert decode_text("Olá".encode("utf-16")) == "Olá"


def test_vtt_cue_settings_are_stripped():
    out = to_vtt(b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000 align:start position:0%\nHi\n",
                 ".vtt")
    assert "align" not in out and "00:00:00.000 --> 00:00:02.000\nHi" in out


def test_language_codes_and_names():
    assert language_code("eng") == "en"
    assert language_code("pt_br") == "pt-BR"
    assert language_code("und") == ""
    assert language_code("../x") == ""
    assert language_label("por") == "Português"
    assert language_label("pt-BR") == "Português (Brasil)"
    assert language_label("") == "Subtitles"


def test_uploaded_video_subtitles_are_listed_and_served(app, authed_client):
    conn = next(app.dependency_overrides[get_db]())
    VideoRepository(conn).insert(Video(
        video_id="up_0123456789abcdef", title="Home", original_title="Home",
        status="active", download_status="ready", storage_mode="uploaded"))
    vdir = app.state.config.data_dir / "uploads" / "up_0123456789abcdef"
    vdir.mkdir(parents=True)
    (vdir / "video.mp4").write_bytes(b"x")
    (vdir / "video.pt-BR.srt").write_bytes(SRT.encode())

    tracks = authed_client.get("/media/subs/up_0123456789abcdef").json()
    assert tracks == [{"lang": "pt-BR", "label": "Português (Brasil)",
                       "url": "/media/subs/up_0123456789abcdef/video.pt-BR.srt"}]
    resp = authed_client.get(tracks[0]["url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/vtt")
    assert resp.text.startswith("WEBVTT")
    assert authed_client.get("/media/subs/up_0123456789abcdef/..%2Fvideo.mp4").status_code == 404
