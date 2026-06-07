from __future__ import annotations
"""Comment service — family comments with channel-scoped visibility."""

import json
from app.models.comment import Comment
from app.models.viewer import ViewerContext
from app.repositories.comment_repo import CommentRepository
from app.repositories.event_repo import EventRepository
from app.services.rate_limit import RateLimiter, RateLimitExceeded

MAX_COMMENT_LENGTH = 500

# Module-level singletons so the rate-limit state survives across
# request-scoped CommentService instances. Kids get 10 comments per
# minute; the parent uses a separate limiter with a higher ceiling
# for legitimate moderation bursts.
_KID_RATE_LIMITER = RateLimiter(max_events=10, window_seconds=60)
_PARENT_RATE_LIMITER = RateLimiter(max_events=30, window_seconds=60)


class CommentService:
    def __init__(self, comment_repo: CommentRepository,
                 event_repo: EventRepository):
        self.comment_repo = comment_repo
        self.event_repo = event_repo

    def list_for_video(self, video_id: str, viewer: ViewerContext,
                       page: int = 1,
                       per_page: int = 20
                       ) -> tuple[list[Comment], int]:
        """Get threaded comments visible to this viewer, paginated by
        top-level comment (replies travel with their parent — no thread
        is ever split across pages). Returns (threaded_list, total_top_level).
        """
        offset = max(0, (page - 1) * per_page)
        if viewer.is_parent:
            top_level = self.comment_repo.list_top_level_for_video(
                video_id, limit=per_page, offset=offset)
            total = self.comment_repo.count_top_level_for_video(video_id)
        elif viewer.profile_id:
            top_level = self.comment_repo.list_top_level_visible_to(
                video_id, viewer.profile_id,
                limit=per_page, offset=offset)
            total = self.comment_repo.count_top_level_visible_to(
                video_id, viewer.profile_id)
        else:
            return [], 0

        replies = self.comment_repo.list_replies_for([c.id for c in top_level])
        replies_map: dict[int, list[Comment]] = {}
        for r in replies:
            replies_map.setdefault(r.parent_comment_id, []).append(r)
        for c in top_level:
            c.replies = replies_map.get(c.id, [])
        return top_level, total

    def post(self, video_id: str, body: str, viewer: ViewerContext,
             parent_comment_id: int | None = None) -> int | None:
        """Post a comment. Returns comment ID or None if invalid.
        Raises RateLimitExceeded if the poster is over quota."""
        body = body.strip()
        if not body or len(body) > MAX_COMMENT_LENGTH:
            return None

        # Rate-limit keyed on poster identity. Parents get a separate,
        # more generous limiter so moderation-style bursts don't trip it.
        if viewer.is_parent:
            key = "parent"
            limiter = _PARENT_RATE_LIMITER
        elif viewer.profile_id:
            key = viewer.profile_id
            limiter = _KID_RATE_LIMITER
        else:
            return None  # anonymous can't post
        if not limiter.check(key):
            raise RateLimitExceeded(
                "Too many comments in a short time — try again in a minute.")

        # Reply-to-reply flattening: threading is one level deep, so if the
        # target is itself a reply, rebind parent_comment_id to the root
        # top-level comment and prepend @author so the reader still knows
        # whose reply this was answering.
        if parent_comment_id:
            target = self.comment_repo.get_by_id(parent_comment_id)
            if target and target.parent_comment_id:
                mention = f"@{target.author_name} "
                if not body.startswith(mention):
                    body = (mention + body)[:MAX_COMMENT_LENGTH]
                parent_comment_id = target.parent_comment_id

        is_parent_user = 1 if viewer.is_parent else 0
        profile_id = viewer.profile_id

        comment_id = self.comment_repo.create(
            video_id, body, profile_id, is_parent_user, parent_comment_id)

        event_type = "comment_reply" if parent_comment_id else "comment_post"
        data = {"comment_id": comment_id}
        if parent_comment_id:
            data["parent_comment_id"] = parent_comment_id
        self.event_repo.insert_raw(
            event_type, video_id, profile_id, json.dumps(data))

        return comment_id

    def get(self, comment_id: int) -> Comment | None:
        """Fetch a single comment (with author info) by id. Used by the
        watch page to render a just-posted comment/reply for its XHR
        response so it can be inserted without a full reload."""
        return self.comment_repo.get_by_id(comment_id)

    def delete(self, comment_id: int) -> None:
        """Delete a comment and its replies."""
        self.comment_repo.delete(comment_id)

    def list_recent(self, limit: int = 50) -> list[Comment]:
        """Get recent comments for parent moderation."""
        return self.comment_repo.list_recent(limit)

    def _thread(self, flat: list[Comment]) -> list[Comment]:
        """Organize flat comment list into threaded structure (1 level)."""
        top_level = []
        replies_map: dict[int, list[Comment]] = {}

        for c in flat:
            if c.parent_comment_id:
                if c.parent_comment_id not in replies_map:
                    replies_map[c.parent_comment_id] = []
                replies_map[c.parent_comment_id].append(c)
            else:
                top_level.append(c)

        for c in top_level:
            c.replies = replies_map.get(c.id, [])

        return top_level
