from dataclasses import dataclass


@dataclass
class Video:
    video_id: str                  # composite: "{extractor}_{sanitised_raw_id}"
    title: str
    original_title: str
    extractor: str = ""            # yt-dlp extractor key, e.g. "youtube", "vimeo"
    original_url: str = ""         # canonical URL on the source platform
    channel_name: str = ""
    description: str = ""
    duration: int = 0
    upload_date: str = ""
    view_count: int = 0
    thumbnail_url: str = ""
    thumbnail_type: str = "original"
    status: str = "active"
    download_status: str = "pending"   # "pending" | "downloading" | "ready" | "error" | "evicted"
    download_error: str = ""
    storage_mode: str = "cache"
    resolution: str = "720p"
    source_id: int | None = None
    channel_id: int | None = None
    added_at: str = ""
    cached_at: str | None = None
    cache_expires_at: str | None = None
    file_size: int = 0
    keep_forever: bool = False
    id: int | None = None

    @property
    def raw_id(self) -> str:
        """Return the extractor-scoped ID (what yt-dlp and embed URLs want).

        The stored `video_id` is a composite `{extractor}_{raw}` string
        — globally unique, filesystem-safe, URL-safe. Most code paths
        (DB lookups, filesystem paths, `/media/video/<id>` routes) use
        the composite. But embed URLs, the yt-dlp re-fetch path, and
        any "open on the source" link need the unprefixed raw ID back.
        """
        prefix = f"{self.extractor}_"
        if self.extractor and self.video_id.startswith(prefix):
            return self.video_id[len(prefix):]
        return self.video_id
