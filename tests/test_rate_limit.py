"""Tests for the sliding-window rate limiter and its integration with
CommentService."""

import threading
import time
from unittest.mock import patch

import pytest

from app.models import Profile, Video
from app.models.viewer import ViewerContext
from app.repositories import ProfileRepository, VideoRepository
from app.repositories.comment_repo import CommentRepository
from app.repositories.event_repo import EventRepository
from app.services.comments import (
    CommentService, _KID_RATE_LIMITER, _PARENT_RATE_LIMITER,
)
from app.services.rate_limit import RateLimiter, RateLimitExceeded


@pytest.fixture(autouse=True)
def _reset_module_limiters():
    _KID_RATE_LIMITER.reset()
    _PARENT_RATE_LIMITER.reset()
    yield


class TestRateLimiter:
    def test_allows_up_to_max(self):
        lim = RateLimiter(max_events=5, window_seconds=60)
        for _ in range(5):
            assert lim.check("alice") is True
        # Sixth in the same window is denied
        assert lim.check("alice") is False

    def test_per_key_independence(self):
        lim = RateLimiter(max_events=3, window_seconds=60)
        for _ in range(3):
            assert lim.check("alice") is True
        # Bob is on a separate budget
        for _ in range(3):
            assert lim.check("bob") is True
        # But both are now at the ceiling
        assert lim.check("alice") is False
        assert lim.check("bob") is False

    def test_window_expires(self):
        lim = RateLimiter(max_events=2, window_seconds=10)
        with patch("app.services.rate_limit.time.monotonic") as mock_t:
            mock_t.return_value = 100.0
            assert lim.check("x") is True
            assert lim.check("x") is True
            assert lim.check("x") is False
            # Jump past the window — old entries pruned, budget refreshed
            mock_t.return_value = 200.0
            assert lim.check("x") is True
            assert lim.check("x") is True
            assert lim.check("x") is False

    def test_thread_safe(self):
        lim = RateLimiter(max_events=100, window_seconds=60)
        barrier = threading.Barrier(50)
        results = []

        def worker():
            barrier.wait()
            for _ in range(4):
                results.append(lim.check("shared"))

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 50 threads × 4 attempts = 200 calls; 100 should pass, 100 deny
        assert results.count(True) == 100
        assert results.count(False) == 100


class TestCommentRateLimit:
    def _svc(self, db):
        return CommentService(CommentRepository(db), EventRepository(db))

    def _seed_video(self, db):
        VideoRepository(db).insert(Video(
            video_id="rlV", title="T", original_title="T",
            status="active", download_status="ready",
        ))

    def _mk_kid(self, db, name="k"):
        return ProfileRepository(db).create(Profile(
            name=name, display_name=name, pin="", allowed_channel_ids=[],
        ))

    def test_kid_hits_kid_limit(self, db):
        self._seed_video(db)
        svc = self._svc(db)
        kid_id = self._mk_kid(db)
        kid = ViewerContext(
            viewer_type="child", profile_id=kid_id, profile_name="k",
            allowed_channel_ids=None)
        for i in range(10):
            svc.post("rlV", f"msg-{i}", kid)
        with pytest.raises(RateLimitExceeded):
            svc.post("rlV", "one-too-many", kid)

    def test_parent_hits_parent_limit(self, db):
        self._seed_video(db)
        svc = self._svc(db)
        parent = ViewerContext(viewer_type="parent")
        for i in range(30):
            svc.post("rlV", f"msg-{i}", parent)
        with pytest.raises(RateLimitExceeded):
            svc.post("rlV", "over", parent)

    def test_separate_kids_have_separate_budgets(self, db):
        self._seed_video(db)
        svc = self._svc(db)
        alice_id = self._mk_kid(db, "alice")
        bob_id = self._mk_kid(db, "bob")
        alice = ViewerContext(
            viewer_type="child", profile_id=alice_id, profile_name="alice",
            allowed_channel_ids=None)
        bob = ViewerContext(
            viewer_type="child", profile_id=bob_id, profile_name="bob",
            allowed_channel_ids=None)
        for _ in range(10):
            svc.post("rlV", "a", alice)
        # Alice is at her cap but Bob's budget is untouched
        with pytest.raises(RateLimitExceeded):
            svc.post("rlV", "a-over", alice)
        for _ in range(10):
            svc.post("rlV", "b", bob)
