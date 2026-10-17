"""Tests for StatsService.dashboard_overview and the parent dashboard
route."""

from app.models import Profile, Video
from app.repositories import (
    EventRepository, ProfileRepository, VideoRepository,
)
from app.repositories.comment_repo import CommentRepository
from app.repositories.reaction_repo import ReactionRepository
from app.services.comments import CommentService
from app.services.stats import StatsService


def _mk_stats(db):
    return StatsService(
        EventRepository(db), CommentRepository(db),
        ReactionRepository(db), ProfileRepository(db),
        CommentService(CommentRepository(db), EventRepository(db)),
        video_repo=VideoRepository(db),
    )


class TestDashboardOverview:
    def test_failed_and_stuck_surfaced(self, db):
        # Seed via insert, then backdate added_at to 3h ago via SQL
        # (UTC, same reference the repo uses).
        VideoRepository(db).insert(Video(
            video_id="fail1", title="Failed", original_title="Failed",
            download_status="error", storage_mode="cache",
        ))
        VideoRepository(db).insert(Video(
            video_id="stuck1", title="Stuck", original_title="Stuck",
            download_status="pending", storage_mode="cache",
        ))
        VideoRepository(db).insert(Video(
            video_id="ok1", title="Ready", original_title="Ready",
            download_status="ready", storage_mode="cache",
        ))
        db.execute(
            "UPDATE videos SET added_at = datetime('now', '-3 hours') "
            "WHERE video_id IN ('fail1', 'stuck1')"
        )
        db.commit()
        ov = _mk_stats(db).dashboard_overview()
        fails = [v.video_id for v in ov["attention"]["failed_downloads"]]
        stucks = [v.video_id for v in ov["attention"]["stuck_pending"]]
        assert fails == ["fail1"]
        assert stucks == ["stuck1"]

    def test_recent_pending_is_not_stuck(self, db):
        """A pending video added in the last hour should NOT show up
        as stuck — it's still within normal download window."""
        VideoRepository(db).insert(Video(
            video_id="fresh", title="Fresh", original_title="Fresh",
            download_status="pending", storage_mode="cache",
            # added_at defaults to now via the schema
        ))
        ov = _mk_stats(db).dashboard_overview()
        assert ov["attention"]["stuck_pending"] == []

    def test_per_kid_includes_last_watched_and_last_comment(self, db):
        alice = ProfileRepository(db).create(Profile(
            name="alice", display_name="Alice", pin="",
            allowed_channel_ids=[],
        ))
        VideoRepository(db).insert(Video(
            video_id="v1", title="Airplanes", original_title="Airplanes",
            status="active", download_status="ready",
        ))
        EventRepository(db).insert_raw(
            "video_complete", "v1", alice, '{"watch_seconds": 300}')
        CommentRepository(db).create("v1", "so cool", alice, 0, None)

        ov = _mk_stats(db).dashboard_overview()
        kids = {k["profile"].id: k for k in ov["per_kid"]}
        assert alice in kids
        k = kids[alice]
        assert k["completions_today"] == 1
        assert k["last_watched"] is not None
        assert k["last_watched"].video_id == "v1"
        assert k["last_comment"] is not None
        assert k["last_comment"].body == "so cool"

    def test_recent_videos_newest_first_and_capped_at_five(self, db):
        for i in range(7):
            VideoRepository(db).insert(Video(
                video_id=f"v{i:02d}", title=f"Video {i}",
                original_title=f"Video {i}",
                status="active", download_status="ready",
            ))
        ov = _mk_stats(db).dashboard_overview()
        assert len(ov["recent_videos"]) == 5

    def test_dashboard_route_renders_with_no_data(self, authed_client):
        resp = authed_client.get("/parent/")
        assert resp.status_code == 200
        assert b"Dashboard" in resp.content
        assert b"Quick add" in resp.content

    def test_dashboard_shows_failed_download_alert(self, authed_client, app):
        from app.dependencies import get_db
        conn = next(app.dependency_overrides[get_db]())
        VideoRepository(conn).insert(Video(
            video_id="failvid", title="Broken Clip",
            original_title="Broken Clip",
            download_status="error", storage_mode="cache",
        ))
        resp = authed_client.get("/parent/")
        assert resp.status_code == 200
        assert b"Needs your attention" in resp.content
        assert b"download" in resp.content
        assert b"Broken Clip" in resp.content

    def test_recently_added_says_where_a_video_came_from(self, authed_client, app):
        from app.dependencies import get_db
        conn = next(app.dependency_overrides[get_db]())
        repo = VideoRepository(conn)
        repo.insert(Video(video_id="up_drawing", title="My drawing", original_title="My drawing",
                          download_status="ready", storage_mode="uploaded"))
        repo.insert(Video(video_id="yt_llama", title="Llamas", original_title="Llamas",
                          channel_name="Blender", download_status="ready"))
        conn.commit()
        recent = authed_client.get("/parent/").text.split("Recently added")[1]
        assert "Uploaded ·" in recent and "Blender ·" in recent
        assert "no channel" not in recent


class TestStatsPage:
    def _seed(self, db):
        videos = VideoRepository(db)
        for vid, title in (("zoo", "Me at the zoo"), ("snail", "Garden snail race"),
                           ("volcano", "Baking soda volcano")):
            videos.insert(Video(video_id=vid, title=title, original_title=title,
                                download_status="ready"))
        profiles = ProfileRepository(db)
        theo = profiles.create(Profile(name="theo", display_name="Theo"))
        lia = profiles.create(Profile(name="lia", display_name="Lia"))
        maya = profiles.create(Profile(name="maya", display_name="Maya"))
        events = EventRepository(db)
        events.insert_raw("video_view", "zoo", theo)
        events.insert_raw("video_complete", "zoo", theo, '{"watch_seconds": 19}')
        events.insert_raw("video_view", "volcano", maya)
        ReactionRepository(db).upsert(lia, "snail", "heart")
        CommentRepository(db).create("volcano", "Legal!", maya)
        return theo, lia, maya

    def test_every_kid_has_a_row(self, db):
        self._seed(db)
        rows = {r["profile_name"]: r for r in _mk_stats(db).per_profile_summary("all")}
        assert list(rows) == ["Theo", "Lia", "Maya"]
        assert rows["Lia"]["reaction_count"] == 1 and rows["Lia"]["completions"] == 0
        assert rows["Maya"]["comment_count"] == 1

    def test_top_videos_include_opened_and_reacted(self, db):
        _theo, lia, _maya = self._seed(db)
        top = _mk_stats(db).top_videos("all")
        assert [v["video_id"] for v in top] == ["zoo", "volcano", "snail"]
        assert (top[0]["plays"], top[0]["opens"], top[0]["secs"]) == (1, 1, 19)
        assert top[2]["reactions"] == {"heart": 1}
        assert [v["video_id"] for v in _mk_stats(db).top_videos("all", profile_id=lia)] == ["snail"]
        # A kid's page counts that kid's reactions only.
        ReactionRepository(db).upsert(_theo, "zoo", "funny")
        assert _mk_stats(db).top_videos("all", profile_id=_maya)[0]["reactions"] == {}
        assert _mk_stats(db).top_videos("all", profile_id=_theo)[0]["reactions"] == {"funny": 1}

    def test_window_leaves_out_older_activity(self, db):
        self._seed(db)
        db.execute("UPDATE events SET timestamp = datetime('now', '-30 days')")
        db.execute("UPDATE reactions SET created_at = datetime('now', '-30 days')")
        db.commit()
        assert _mk_stats(db).top_videos("7d") == []
        assert {r["profile_name"] for r in _mk_stats(db).per_profile_summary("7d")} == {"Theo", "Lia", "Maya"}


def test_dashboard_marks_a_kid_upload_waiting_for_approval(app, authed_client):
    from app.dependencies import get_db
    conn = next(app.dependency_overrides[get_db]())
    VideoRepository(conn).insert(Video(video_id="up_d", title="My drawing", original_title="My drawing",
                                       download_status="ready", status="review"))
    page = authed_client.get("/parent/").text
    recent = page.split("Recently added")[1]
    assert "waiting for your OK" in recent
    assert "badge-ready" not in recent


def test_dashboard_shows_kid_avatars_and_links_whole_lines(app, authed_client):
    from app.dependencies import get_db
    conn = next(app.dependency_overrides[get_db]())
    ProfileRepository(conn).create(Profile(name="lia", display_name="Lia", avatar="unicorn"))
    ProfileRepository(conn).create(Profile(name="sam", display_name="Sam"))
    page = authed_client.get("/parent/").text
    assert "\U0001f984" in page          # Lia's unicorn
    assert '<div class="avatar" aria-hidden="true">S</div>' in page
    assert "Upload a video" in page
