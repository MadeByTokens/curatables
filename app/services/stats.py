"""Stats service — aggregated usage statistics for the parent dashboard."""

from __future__ import annotations

from app.repositories.event_repo import EventRepository
from app.repositories.comment_repo import CommentRepository
from app.repositories.reaction_repo import ReactionRepository
from app.repositories.profile_repo import ProfileRepository
from app.repositories.video_repo import VideoRepository
from app.models.viewer import ViewerContext
from app.services.comments import CommentService
from app.services.family_time import since_days_ago


class StatsService:
    def __init__(self, event_repo: EventRepository,
                 comment_repo: CommentRepository,
                 reaction_repo: ReactionRepository,
                 profile_repo: ProfileRepository,
                 comment_service: CommentService,
                 video_repo: VideoRepository,
                 timezone: str = ""):
        self.event_repo = event_repo
        self.comment_repo = comment_repo
        self.reaction_repo = reaction_repo
        self.profile_repo = profile_repo
        self.comment_service = comment_service
        self.video_repo = video_repo
        # The family's zone: "today" starts at their midnight, not UTC's.
        self.timezone = timezone

    def _since_date(self, window: str) -> str | None:
        """Start of the window, as stored UTC text."""
        if window == "today":
            return since_days_ago(0, self.timezone)
        if window == "7d":
            return since_days_ago(7, self.timezone)
        return None

    def dashboard_overview(self) -> dict:
        """Everything the parent home dashboard renders: today's KPIs,
        per-kid snapshots, recent videos/comments and a 'needs attention'
        bucket. All lists are bounded."""
        failed = self.video_repo.list_failed_downloads(limit=10)
        stuck = self.video_repo.list_stuck_pending(older_than_hours=1, limit=10)
        recent_videos = self.video_repo.list_recent(limit=5)
        awaiting_review = self.video_repo.list_awaiting_review(limit=10)

        yesterday_iso = since_days_ago(1, self.timezone)
        new_comments_count = self.comment_repo.count_since(since=yesterday_iso)

        kpis_today = self.dashboard_kpis("today")

        profiles = self.profile_repo.list()
        today_iso = since_days_ago(0, self.timezone)
        per_kid = []
        for p in profiles:
            if p.id is None:
                continue
            last_watched_id = self.event_repo.last_watched_video_id(p.id)
            last_watched = (self.video_repo.get(last_watched_id)
                            if last_watched_id else None)
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
                "awaiting_review": awaiting_review,
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
        """Videos opened, finished or reacted to in the window: most
        finished first, then most opened, then most reacted to."""
        since = self._since_date(window)
        rows = {r["video_id"]: r for r in self.event_repo.video_activity(
            profile_id=profile_id, since=since)}
        for vid in self.reaction_repo.video_ids_since(profile_id=profile_id, since=since):
            if vid not in rows:
                video = self.video_repo.get(vid)
                rows[vid] = {"video_id": vid, "plays": 0, "secs": 0, "opens": 0,
                             "video_title": video.title if video else None}
        reaction_map = self.reaction_repo.counts_for_videos(list(rows))
        for r in rows.values():
            r["reactions"] = reaction_map.get(r["video_id"], {})
        ranked = sorted(rows.values(), key=lambda r: (
            -r["plays"], -r["opens"], -sum(r["reactions"].values()),
            r["video_title"] or ""))
        return ranked[:limit]

    def per_profile_summary(self, window: str) -> list[dict]:
        """Every kid, with zeros for a kid who did nothing in the window."""
        since = self._since_date(window)
        watched = {r["profile_id"]: r for r in self.event_repo.stats_per_profile(since=since)}
        rows = []
        for p in self.profile_repo.list():
            if p.id is None:
                continue
            w = watched.get(p.id, {})
            rows.append({
                "profile_id": p.id,
                "profile_name": p.display_name or p.name,
                "avatar": p.avatar or "default",
                "completions": w.get("completions", 0),
                "secs": w.get("secs", 0),
                "reaction_count": self.reaction_repo.count_by_profile(p.id, since=since),
                "comment_count": self.comment_repo.count_by_profile(p.id, since=since),
            })
        rows.sort(key=lambda r: (-r["completions"], r["profile_name"].lower()))
        return rows

    def video_detail(self, video_id: str, viewer: ViewerContext,
                     comments_page: int = 1,
                     comments_per_page: int = 20) -> dict:
        stats = self.event_repo.video_stats(video_id)
        page_views = self.event_repo.count_events(
            "video_view", video_id=video_id)
        reactions_with_names = self.reaction_repo.list_for_video_with_profiles(
            video_id)
        reaction_counts = self.reaction_repo.counts_for_video(video_id)
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
            "comments_count": self.comment_service.count_for_video(video_id, viewer),
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
