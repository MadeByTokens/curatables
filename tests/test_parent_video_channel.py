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


def test_approving_a_kid_upload_says_where_kids_see_it(app, authed_client):
    conn = next(app.dependency_overrides[get_db]())
    animals = ChannelRepository(conn).create("Animals")
    VideoRepository(conn).insert(Video(video_id="up_d", title="My drawing", original_title="My drawing",
                                       download_status="ready", status="review", channel_id=animals))
    resp = authed_client.post("/parent/content/up_d/activate", follow_redirects=True)
    assert "Approved &#34;My drawing&#34;. Kids see it in Animals." in resp.text
    assert VideoRepository(conn).get("up_d").status == "active"


def test_channel_changes_are_confirmed(authed_client):
    page = authed_client.post("/parent/channels/create", data={"name": "Bedtime"},
                              follow_redirects=True).text
    assert "Channel created." in page
    page = authed_client.post("/parent/channels/create", data={"name": "Bedtime"},
                              follow_redirects=True).text
    assert "already exists" in page
    assert authed_client.get("/parent/channels/?notice=<b>x</b>").text.count('role="status"') == 0


def test_unhiding_says_unhid(app, authed_client):
    conn = next(app.dependency_overrides[get_db]())
    VideoRepository(conn).insert(Video(video_id="yt_h", title="Hidden one", original_title="h",
                                       download_status="ready", status="hidden"))
    resp = authed_client.post("/parent/content/yt_h/activate", follow_redirects=True)
    assert "Unhid &#34;Hidden one&#34;. It has no channel" in resp.text


def test_content_list_names_channels_not_internal_ids(app, authed_client):
    conn = next(app.dependency_overrides[get_db]())
    animals = ChannelRepository(conn).create("Animals")
    VideoRepository(conn).insert(Video(video_id="up_e892a1", title="Garden snail race",
                                       original_title="g", download_status="ready", channel_id=animals))
    VideoRepository(conn).insert(Video(video_id="youtube_jNQXAC9IVRw", title="Me at the zoo",
                                       original_title="z", download_status="ready", channel_name="jawed",
                                       status="review"))
    page = authed_client.get("/parent/content").text
    assert "<small>up_e892a1</small>" not in page and "<small>youtube_jNQXAC9IVRw</small>" not in page
    snail = page[page.index("Garden snail race"):page.index("Garden snail race") + 400]
    assert "Animals" in snail and "&middot; from" not in snail
    zoo = page[page.index("Me at the zoo"):]
    assert "No channel" in zoo and "from jawed" in zoo and "Waiting for your OK" in zoo
    assert 'id="bulk-actions"' in page


def test_channels_page_counts_and_hides_export_for_empty(app, authed_client):
    conn = next(app.dependency_overrides[get_db]())
    animals = ChannelRepository(conn).create("Animals")
    ChannelRepository(conn).create("Bedtime")
    VideoRepository(conn).insert(Video(video_id="yt_z", title="z", original_title="z",
                                       download_status="ready", channel_id=animals))
    page = authed_client.get("/parent/channels/").text
    assert "1 video\n" in page or "1 video<" in page or "1 video " in page
    assert "video(s)" not in page
    assert "No videos yet" in page
    assert page.count("export?format=ytc") == 1
