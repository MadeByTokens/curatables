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
    last_played_at: str | None = None
    play_count: int = 0
    origin: str = ""               # import: "movie" | "youtube" | "web" | "home"
    origin_ref: str = ""           # import: Wikidata / TMDB / YouTube id
    source_path: str = ""          # import: the file it came from
    episode_key: int | None = None  # season * 1000 + episode; orders a show's channel
    id: int | None = None

    @property
    def raw_id(self) -> str:
        """The platform's own ID, without the `{extractor}_` prefix.

        The composite `video_id` is used for storage, paths and routes;
        embed URLs, yt-dlp and links to the source need the raw ID.
        """
        prefix = f"{self.extractor}_"
        if self.extractor and self.video_id.startswith(prefix):
            return self.video_id[len(prefix):]
        return self.video_id
