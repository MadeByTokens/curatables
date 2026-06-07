from __future__ import annotations
"""Stats service — aggregated usage statistics for the parent dashboard."""

from datetime import date, timedelta

from app.repositories.event_repo import EventRepository
from app.repositories.comment_repo import CommentRepository
from app.repositories.reaction_repo import ReactionRepository
from app.repositories.profile_repo import ProfileRepository
from app.repositories.video_repo import VideoRepository
from app.services.comments import CommentService


class StatsService:
    def __init__(self, event_repo: EventRepository,
                 comment_repo: CommentRepository,
                 reaction_repo: ReactionRepository,
                 profile_repo: ProfileRepository,
                 comment_service: CommentService,
                 video_repo: VideoRepository | None = None):
        self.event_repo = event_repo
        self.comment_repo = comment_repo
        self.reaction_repo = reaction_repo
        self.profile_repo = profile_repo
        self.comment_service = comment_service
        self.video_repo = video_repo

    @staticmethod
    def _since_date(window: str) -> str | None:
        if window == "today":
            return date.today().isoformat()
        if window == "7d":
            return (date.today() - timedelta(days=7)).isoformat()
        return None

    def dashboard_overview(self) -> dict:
        """One-stop fetch for the parent home dashboard. Composes KPIs,
        per-kid snapshots, recent videos/comments, and a 'needs attention'
        bucket. All lists are bounded. Meant to be called once per
        dashboard render — ~10 small queries total."""
        failed = (self.video_repo.list_failed_downloads(limit=10)
                  if self.video_repo else [])
        stuck = (self.video_repo.list_stuck_pending(older_than_hours=1,
                                                    limit=10)
                 if self.video_repo else [])
        recent_videos = (self.video_repo.list_recent(limit=5)
                         if self.video_repo else [])

        yesterday_iso = (date.today() - timedelta(days=1)).isoformat()
        new_comments_count = self.comment_repo.count_since(since=yesterday_iso)

        kpis_today = self.dashboard_kpis("today")

        # Per-kid summary. For each profile, attach last-watched video
        # and last comment so the dashboard can show a "what they're
        # doing" line without extra round-trips on the template side.
        profiles = self.profile_repo.list()
        today_iso = date.today().isoformat()
        per_kid = []
        for p in profiles:
            last_watched_id = self.event_repo.last_watched_video_id(p.id)
            last_watched = (self.video_repo.get(last_watched_id)
                            if last_watched_id and self.video_repo else None)
            last_comments = self.comment_repo.list_recent_by_profile(
                p.id, limit=1)
            per_kid.append({
                "profile": p,
                "completions_today": self.event_repo.count_completions(
                    profile_id=p.id, since=today_iso),
                "watch_seconds_today": self.event_repo.watch_seconds(
                    profile_id=p.id, since=today_iso),
                "comments_today": self.comment_repo.count_by_profile(
                    p.id, since=today_iso),
                "last_watched": last_watched,
                "last_comment": last_comments[0] if last_comments else None,
            })

        recent_comments = self.comment_repo.list_recent(limit=5)

        return {
            "attention": {
                "failed_downloads": failed,
                "stuck_pending": stuck,
                "new_comments_count": new_comments_count,
            },
            "kpis_today": kpis_today,
            "per_kid": per_kid,
            "recent_videos": recent_videos,
            "recent_comments": recent_comments,
        }

    def dashboard_kpis(self, window: str) -> dict:
        since = self._since_date(window)
        return {
            "page_views": self.event_repo.count_events(
                "video_view", since=since),
            "completions": self.event_repo.count_completions(since=since),
            "watch_seconds": self.event_repo.watch_seconds(since=since),
            "comment_count": self.comment_repo.count_since(since=since),
            "reaction_count": self.reaction_repo.count_since(since=since),
        }

    def top_videos(self, window: str, profile_id: int | None = None,
                   limit: int = 10) -> list[dict]:
        since = self._since_date(window)
        rows = self.event_repo.top_videos_by_completions(
            profile_id=profile_id, since=since, limit=limit)
        video_ids = [r["video_id"] for r in rows if r.get("video_id")]
        reaction_map = self.reaction_repo.counts_for_videos(video_ids)
        for r in rows:
            r["reactions"] = reaction_map.get(r["video_id"], {})
        return rows

    def per_profile_summary(self, window: str) -> list[dict]:
        since = self._since_date(window)
        rows = self.event_repo.stats_per_profile(since=since)
        for r in rows:
            pid = r["profile_id"]
            r["reaction_count"] = self.reaction_repo.count_by_profile(
                pid, since=since)
            r["comment_count"] = self.comment_repo.count_by_profile(
                pid, since=since)
        return rows

    def video_detail(self, video_id: str, viewer=None,
                     comments_page: int = 1,
                     comments_per_page: int = 20) -> dict:
        stats = self.event_repo.video_stats(video_id)
        page_views = self.event_repo.count_events(
            "video_view", video_id=video_id)
        reactions_with_names = self.reaction_repo.list_for_video_with_profiles(
            video_id)
        reaction_counts = self.reaction_repo.counts_for_video(video_id)
        # Parent moderation view — use a parent ViewerContext if none
        # was supplied so we see every comment (not channel-scoped).
        if viewer is None:
            from app.models.viewer import ViewerContext
            viewer = ViewerContext(viewer_type="parent")
        comments, comments_total = self.comment_service.list_for_video(
            video_id, viewer, page=comments_page, per_page=comments_per_page)
        comments_total_pages = max(
            1, (comments_total + comments_per_page - 1) // comments_per_page)
        return {
            **stats,
            "page_views": page_views,
            "reactions_with_names": reactions_with_names,
            "reaction_counts": reaction_counts,
            "comments": comments,
            "comments_page": comments_page,
            "comments_total": comments_total,
            "comments_total_pages": comments_total_pages,
        }

    def profile_detail(self, profile_id: int, window: str) -> dict:
        since = self._since_date(window)
        profile = self.profile_repo.get(profile_id)
        return {
            "profile": profile,
            "page_views": self.event_repo.count_events(
                "video_view", profile_id=profile_id, since=since),
            "completions": self.event_repo.count_completions(
                profile_id=profile_id, since=since),
            "watch_seconds": self.event_repo.watch_seconds(
                profile_id=profile_id, since=since),
            "reaction_count": self.reaction_repo.count_by_profile(
                profile_id, since=since),
            "comment_count": self.comment_repo.count_by_profile(
                profile_id, since=since),
            "top_videos": self.top_videos(window, profile_id=profile_id),
            "recent_reactions": self.reaction_repo.list_by_profile(
                profile_id, limit=20),
            "recent_comments": self.comment_repo.list_recent_by_profile(
                profile_id, limit=20),
        }
