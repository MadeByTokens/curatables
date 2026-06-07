from dataclasses import dataclass


@dataclass
class Source:
    source_type: str       # "channel" | "playlist" | "video"
    extractor: str         # yt-dlp extractor key, e.g. "youtube", "vimeo"
    external_id: str       # raw ID assigned by the source platform
    title: str
    url: str
    description: str = ""
    auto_sync: bool = False
    status: str = "active"
    metadata_json: str = "{}"
    added_at: str = ""
    id: int | None = None
