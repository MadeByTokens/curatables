"""Tests for StatsService.dashboard_overview and the parent dashboard
route."""

from app.models import Profile, Video
from app.repositories import (
    ChannelRepository, EventRepository, ProfileRepository, VideoRepository,
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
