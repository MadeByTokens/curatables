"""Repository layer tests — SQL correctness against in-memory SQLite."""

from app.models import Video, Profile
from app.repositories import (
    VideoRepository, ChannelRepository, ProfileRepository,
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
