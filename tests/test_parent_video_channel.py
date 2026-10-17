"""Parent edit page: moving a video to "(none)" really takes it out of its channel."""
from app.dependencies import get_db
from app.models import Video
from app.repositories import VideoRepository
from app.repositories.channel_repo import ChannelRepository


def _form(**extra):
    data = {"title": "t", "description": "", "channel_id": "", "new_channel_name": "",
            "resolution": "720p"}
    data.update(extra)
    return data


def test_none_clears_the_channel_and_warns(app, authed_client):
    conn = next(app.dependency_overrides[get_db]())
    songs = ChannelRepository(conn).create("Songs")
    VideoRepository(conn).insert(Video(video_id="yt_a", title="a", original_title="a",
                                       download_status="ready", channel_id=songs))

    page = authed_client.post("/parent/content/yt_a/edit", data=_form()).text
    assert VideoRepository(conn).get("yt_a").channel_id is None
    assert "kids can&#39;t see it" in page

    page = authed_client.post("/parent/content/yt_a/edit", data=_form(channel_id=str(songs))).text
    assert VideoRepository(conn).get("yt_a").channel_id == songs
    assert "kids can&#39;t see it" not in page


def test_detail_page_names_the_family_channel(app, authed_client):
    conn = next(app.dependency_overrides[get_db]())
    animals = ChannelRepository(conn).create("Animals")
    VideoRepository(conn).insert(Video(video_id="up_a", title="My drawing", original_title="My drawing",
                                       download_status="ready", channel_id=animals))
    VideoRepository(conn).insert(Video(video_id="yt_zoo", title="Me at the zoo", original_title="z",
                                       download_status="ready", channel_name="jawed"))
    page = authed_client.get("/parent/content/up_a").text
    assert "Animals</a>" in page and "From " not in page
    page = authed_client.get("/parent/content/yt_zoo").text
    assert "No channel: kids can&rsquo;t see it" in page and "From jawed" in page
