from __future__ import annotations
"""Kid library service — per-kid video overrides, tags, and channel bookmarks."""

import dataclasses
import os
from pathlib import Path

from app.models.tag import Tag
from app.models.video import Video
from app.repositories.override_repo import ProfileVideoOverrideRepository
from app.repositories.tag_repo import TagRepository
from app.repositories.profile_channel_video_repo import ProfileChannelVideoRepository


class KidLibraryService:
    def __init__(self, override_repo: ProfileVideoOverrideRepository,
                 tag_repo: TagRepository,
                 channel_video_repo: ProfileChannelVideoRepository,
                 data_dir: Path):
        self.override_repo = override_repo
        self.tag_repo = tag_repo
        self.channel_video_repo = channel_video_repo
        self.data_dir = data_dir

    # --- Video title/thumbnail overrides ---

    def set_title(self, profile_id: int, video_id: str,
                  title: str) -> None:
        title = title.strip()
        if title:
            self.override_repo.upsert(profile_id, video_id, title=title)
        else:
            self.override_repo.clear_field(profile_id, video_id, "title")

    def set_description(self, profile_id: int, video_id: str,
                        description: str) -> None:
        description = description.strip()
        if description:
            self.override_repo.upsert(
                profile_id, video_id, description=description)
        else:
            self.override_repo.clear_field(
                profile_id, video_id, "description")

    def clear_title(self, profile_id: int, video_id: str) -> None:
        self.override_repo.clear_field(profile_id, video_id, "title")

    def clear_description(self, profile_id: int, video_id: str) -> None:
        self.override_repo.clear_field(profile_id, video_id, "description")

    def upload_thumbnail(self, profile_id: int, video_id: str,
                         file_data: bytes, filename: str) -> None:
        ext = os.path.splitext(filename)[1].lower() or ".jpg"
        thumb_dir = self.data_dir / "thumbnails" / "profiles" / str(profile_id)
        thumb_dir.mkdir(parents=True, exist_ok=True)
        for old in thumb_dir.glob(f"{video_id}.*"):
            old.unlink(missing_ok=True)
        (thumb_dir / f"{video_id}{ext}").write_bytes(file_data)
        self.override_repo.upsert(profile_id, video_id, has_custom_thumb=1)

    def clear_thumbnail(self, profile_id: int, video_id: str) -> None:
        thumb_dir = self.data_dir / "thumbnails" / "profiles" / str(profile_id)
        for old in thumb_dir.glob(f"{video_id}.*"):
            old.unlink(missing_ok=True)
        self.override_repo.upsert(profile_id, video_id, has_custom_thumb=0)

    def get_overrides(self, profile_id: int,
                      video_id: str) -> dict | None:
        return self.override_repo.get(profile_id, video_id)

    def apply_overrides(self, profile_id: int,
                        videos: list[Video]) -> list[Video]:
        """Return a new list of Video instances with this kid's title /
        description overrides applied. The input list is NOT mutated —
        callers that hand in shared/cached Video objects are safe. Uses
        dataclasses.replace to build patched copies only for videos with
        an actual override; unmodified videos are passed through by
        reference to avoid useless allocations."""
        if not videos:
            return videos
        ids = [v.video_id for v in videos]
        overrides = self.override_repo.bulk_get(profile_id, ids)
        out: list[Video] = []
        for v in videos:
            ov = overrides.get(v.video_id)
            changes: dict = {}
            if ov:
                if ov.get("title"):
                    changes["title"] = ov["title"]
                if ov.get("description"):
                    changes["description"] = ov["description"]
            out.append(dataclasses.replace(v, **changes) if changes else v)
        return out

    def get_custom_thumb_path(self, profile_id: int,
                              video_id: str) -> Path | None:
        thumb_dir = self.data_dir / "thumbnails" / "profiles" / str(profile_id)
        matches = list(thumb_dir.glob(f"{video_id}.*"))
        return matches[0] if matches else None

    def has_custom_thumb(self, profile_id: int,
                         video_id: str) -> bool:
        ov = self.override_repo.get(profile_id, video_id)
        return bool(ov and ov.get("has_custom_thumb"))

    # --- Tags ---

    def add_tag(self, profile_id: int, video_id: str,
                tag_name: str) -> int | None:
        """Attach a tag (creating it if new). Returns the tag id, or
        None when the name is blank — the id lets the watch page render
        the new chip from its XHR response without a full reload."""
        tag_name = tag_name.strip()
        if not tag_name:
            return None
        tag_id = self.tag_repo.get_or_create(tag_name)
        self.tag_repo.add_to_video(profile_id, video_id, tag_id)
        return tag_id

    def remove_tag(self, profile_id: int, video_id: str,
                   tag_id: int) -> None:
        self.tag_repo.remove_from_video(profile_id, video_id, tag_id)

    def tags_for_video(self, profile_id: int,
                       video_id: str) -> list[Tag]:
        return self.tag_repo.list_for_video(profile_id, video_id)

    def sync_tags(self, profile_id: int, video_id: str,
                  tag_names: list[str]) -> None:
        self.tag_repo.remove_all_for_video(profile_id, video_id)
        for name in tag_names:
            name = name.strip()
            if name:
                tag_id = self.tag_repo.get_or_create(name)
                self.tag_repo.add_to_video(profile_id, video_id, tag_id)

    def tag_cloud(self, profile_id: int,
                  channel_ids: list[int] | None = None) -> list[dict]:
        return self.tag_repo.tag_cloud(profile_id, channel_ids)

    def videos_by_tag(self, profile_id: int, tag_name: str,
                      channel_ids: list[int] | None = None,
                      page: int = 1, per_page: int = 24
                      ) -> tuple[list[str], int]:
        offset = (page - 1) * per_page
        video_ids = self.tag_repo.list_videos_by_tag(
            profile_id, tag_name, channel_ids, per_page, offset)
        total = self.tag_repo.count_videos_by_tag(
            profile_id, tag_name, channel_ids)
        return video_ids, total

    # --- Channel bookmarks ---

    def bookmark_video(self, profile_id: int, channel_id: int,
                       video_id: str) -> None:
        self.channel_video_repo.add(profile_id, channel_id, video_id)

    def unbookmark_video(self, profile_id: int, channel_id: int,
                         video_id: str) -> None:
        self.channel_video_repo.remove(profile_id, channel_id, video_id)

    def channel_video_ids(self, profile_id: int, channel_id: int,
                          page: int = 1, per_page: int = 24
                          ) -> tuple[list[str], int]:
        offset = (page - 1) * per_page
        ids = self.channel_video_repo.list_video_ids_for_channel(
            profile_id, channel_id, per_page, offset)
        total = self.channel_video_repo.count_for_channel(
            profile_id, channel_id)
        return ids, total

    def channels_for_video(self, profile_id: int,
                           video_id: str) -> list[int]:
        return self.channel_video_repo.channels_for_video(
            profile_id, video_id)
