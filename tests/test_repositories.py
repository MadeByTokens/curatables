"""Repository layer tests — SQL correctness against in-memory SQLite."""

from app.models import Video, Profile
from app.repositories import (
    VideoRepository, ChannelRepository, EventRepository, ProfileRepository,
)


def _make_video(video_id, title="Test", channel_id=None,
                status="active", download_status="ready"):
    return Video(
        video_id=video_id, title=title, original_title=title,
        channel_id=channel_id, status=status, download_status=download_status,
    )


class TestChannelRepository:
    def test_create_and_list(self, db):
        repo = ChannelRepository(db)
        cid = repo.create("Science")
        channels = repo.list()
        assert len(channels) == 1
        assert channels[0].name == "Science"
        assert channels[0].id == cid

    def test_create_duplicate_returns_existing_id(self, db):
        repo = ChannelRepository(db)
        id1 = repo.create("Music")
        id2 = repo.create("Music")
        assert id1 == id2

    def test_list_with_counts(self, db):
        ch_repo = ChannelRepository(db)
        vid_repo = VideoRepository(db)
        cid = ch_repo.create("Art")

        # No videos yet
        result = ch_repo.list_with_counts()
        assert len(result) == 1
        assert result[0][1] == 0

        # Add an active video
        vid_repo.insert(_make_video("abc12345678", channel_id=cid))
        result = ch_repo.list_with_counts()
        assert result[0][1] == 1

    def test_count_videos_excludes_hidden(self, db):
        ch_repo = ChannelRepository(db)
        vid_repo = VideoRepository(db)
        cid = ch_repo.create("Games")

        vid_repo.insert(_make_video("vid00000001", title="Active", channel_id=cid))
        vid_repo.insert(_make_video("vid00000002", title="Hidden", channel_id=cid, status="hidden"))
        assert ch_repo.count_videos(cid) == 1


class TestProfileRepository:
    def test_create_and_get(self, db):
        repo = ProfileRepository(db)
        p = Profile(name="kiddo", display_name="Kiddo", pin="1234")
        pid = repo.create(p)
        loaded = repo.get(pid)
        assert loaded is not None
        assert loaded.name == "kiddo"
        assert loaded.display_name == "Kiddo"

    def test_delete_cascades_channel_permissions(self, db):
        ch_repo = ChannelRepository(db)
        prof_repo = ProfileRepository(db)

        cid = ch_repo.create("Science")
        p = Profile(name="kid1", allowed_channel_ids=[cid])
        pid = prof_repo.create(p)

        # Verify link exists
        rows = db.execute(
            "SELECT * FROM profile_channels WHERE profile_id = ?", (pid,)
        ).fetchall()
        assert len(rows) == 1

        # Delete profile — cascade should remove profile_channels
        prof_repo.delete(pid)
        rows = db.execute(
            "SELECT * FROM profile_channels WHERE profile_id = ?", (pid,)
        ).fetchall()
        assert len(rows) == 0


    def test_a_new_kids_channels_are_saved_for_the_next_request(self, tmp_path):
        """Each request on the box has its own connection; anything left
        uncommitted is gone when it closes."""
        import sqlite3
        from app.db.schema import init_schema

        def connect():
            conn = sqlite3.connect(str(tmp_path / "box.db"))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            return conn

        first = connect()
        init_schema(first)
        cid = ChannelRepository(first).create("Songs")
        pid = ProfileRepository(first).create(Profile(name="leo", allowed_channel_ids=[cid]))
        first.close()

        assert ProfileRepository(connect()).get(pid).allowed_channel_ids == [cid]


class TestProfileNameFallback:
    """A profile with no display name is shown by its name."""

    def test_reactions_and_watch_stats_use_name(self, db):
        from app.repositories.reaction_repo import ReactionRepository

        pid = ProfileRepository(db).create(Profile(name="kiddo"))
        VideoRepository(db).insert(_make_video("v1"))
        ReactionRepository(db).upsert(pid, "v1", "heart")
        EventRepository(db).insert_raw("video_complete", "v1", pid, '{"watch_seconds": 5}')

        reactions = ReactionRepository(db).list_for_video_with_profiles("v1")
        assert [r["profile_name"] for r in reactions] == ["kiddo"]
        stats = EventRepository(db).stats_per_profile()
        assert [s["profile_name"] for s in stats] == ["kiddo"]


class TestVideoSearch:
    def test_search_matches_from_the_start_of_a_word(self, db):
        repo = VideoRepository(db)
        repo.insert(_make_video("v1", title="Park day"))
        repo.insert(_make_video("v2", title="Sparky the dog"))
        repo.insert(_make_video("v3", title="Fun in the (parks)"))
        repo.insert(_make_video("v4", title="Ducks"))
        assert {v.video_id for v in repo.search_ready("park")} == {"v1", "v3"}
        assert {v.video_id for v in repo.search_ready("Park DAY")} == {"v1"}
        assert len(repo.search_ready("park", limit=1, offset=1)) == 1
        assert repo.search_ready("park", channel_ids=[]) == []

    def test_pages_of_a_search_follow_on(self, db):
        repo = VideoRepository(db)
        for i in range(12):
            repo.insert(_make_video(f"v{i:02d}", title=("Park " if i % 2 else "Sparky ") + str(i)))
            db.execute("UPDATE videos SET added_at = datetime('now', ?) WHERE video_id = ?",
                       (f"-{i} minutes", f"v{i:02d}"))
        db.commit()
        pages = [[v.video_id for v in repo.search_ready("park", limit=2, offset=o)]
                 for o in (0, 2, 4, 6)]
        assert pages == [["v01", "v03"], ["v05", "v07"], ["v09", "v11"], []]
