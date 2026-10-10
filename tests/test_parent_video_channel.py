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
