"""Tests for comment pagination, reply-threading, and rate-limiting."""

import pytest

from app.models import Profile, Video
from app.models.viewer import ViewerContext
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository
from app.repositories.comment_repo import CommentRepository
from app.repositories.event_repo import EventRepository
from app.services.comments import (
    CommentService, _KID_RATE_LIMITER, _PARENT_RATE_LIMITER,
)


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Clear both limiters before each test so fixtures don't leak
    counters between tests (25 comments in one test can overflow the
    30-per-minute parent limit of the next)."""
    _KID_RATE_LIMITER.reset()
    _PARENT_RATE_LIMITER.reset()
    yield


def _mk_video(db, video_id="v1"):
    VideoRepository(db).insert(Video(
        video_id=video_id, title="T", original_title="T",
        status="active", download_status="ready",
    ))
    return video_id


def _mk_svc(db):
    return CommentService(CommentRepository(db), EventRepository(db))


def _parent_viewer():
    return ViewerContext(viewer_type="parent")


def _seed_comment(db, video_id, body, parent_comment_id=None,
                  profile_id=None, is_parent_user=1):
    """Insert a comment directly via the repo, bypassing service-level
    rate limits and validation. For tests that need to seed large
    numbers of rows to exercise pagination behavior."""
    return CommentRepository(db).create(
        video_id, body, profile_id, is_parent_user, parent_comment_id)


class TestCommentPagination:
    def test_top_level_paginated(self, db):
        _mk_video(db, "v1")
        svc = _mk_svc(db)
        for i in range(45):
            _seed_comment(db, "v1", f"comment-{i}")

        page1, total = svc.list_for_video(
            "v1", _parent_viewer(), page=1, per_page=20)
        assert total == 45
        assert len(page1) == 20
        # Newest-first ordering
        assert page1[0].body == "comment-44"

        page2, _ = svc.list_for_video(
            "v1", _parent_viewer(), page=2, per_page=20)
        assert len(page2) == 20
        assert page2[0].body == "comment-24"

        page3, _ = svc.list_for_video(
            "v1", _parent_viewer(), page=3, per_page=20)
        assert len(page3) == 5

    def test_replies_travel_with_top_level(self, db):
        _mk_video(db, "v1")
        svc = _mk_svc(db)
        top_id = _seed_comment(db, "v1", "parent-comment")
        for i in range(3):
            _seed_comment(db, "v1", f"reply-{i}",
                          parent_comment_id=top_id)
        # Pad with 30 more top-level comments to push the target onto
        # page 2 (newest-first, so the original is the oldest).
        for i in range(30):
            _seed_comment(db, "v1", f"filler-{i}")

        # The original top-level comment is the oldest → last page
        _, total = svc.list_for_video(
            "v1", _parent_viewer(), page=1, per_page=20)
        # 1 original + 30 fillers = 31 top-level (replies don't count)
        assert total == 31

        page2, _ = svc.list_for_video(
            "v1", _parent_viewer(), page=2, per_page=20)
        # page 2 should contain the oldest 11: fillers 0-10 + the
        # original (the original is the oldest).
        bodies = [c.body for c in page2]
        assert "parent-comment" in bodies

        # The original top-level should have its replies attached
        original = next(c for c in page2 if c.body == "parent-comment")
        assert len(original.replies) == 3

    def test_empty_returns_empty_with_zero_total(self, db):
        _mk_video(db, "v1")
        svc = _mk_svc(db)
        comments, total = svc.list_for_video("v1", _parent_viewer())
        assert comments == []
        assert total == 0

    def test_child_visibility_respects_pagination(self, db):
        """Kids still see only their own comments + parent + channel-mates
        under pagination, and the total reflects only visible top-level."""
        alice = ProfileRepository(db).create(Profile(
            name="alice", display_name="Alice", pin="",
            allowed_channel_ids=[],
        ))
        bob = ProfileRepository(db).create(Profile(
            name="bob", display_name="Bob", pin="",
            allowed_channel_ids=[],
        ))
        _mk_video(db, "v1")
        svc = _mk_svc(db)

        # Bob posts 5 — Alice can't see any (no shared channel, no
        # parent-user flag)
        for i in range(5):
            _seed_comment(db, "v1", f"bob-{i}",
                          profile_id=bob, is_parent_user=0)

        # Alice posts 3 of her own
        for i in range(3):
            _seed_comment(db, "v1", f"alice-{i}",
                          profile_id=alice, is_parent_user=0)

        alice_viewer = ViewerContext(
            viewer_type="child", profile_id=alice, profile_name="alice",
            allowed_channel_ids=None)

        comments, total = svc.list_for_video("v1", alice_viewer)
        assert total == 3
        assert all(c.body.startswith("alice-") for c in comments)
