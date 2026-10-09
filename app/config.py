"""Configuration management for curatables server."""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class ServerConfig:
    port: int = 8080
    host: str = "0.0.0.0"
    log_level: str = "info"  # debug, info, warning, error
    mdns_enabled: bool = True
    mdns_name: str = "Curatables"  # friendly service name advertised over mDNS
    # When True, /metrics exposes Prometheus-format counters (HTTP
    # requests, login outcomes, downloads, evictions). Default off so
    # vanilla installs don't ship a public counter surface; flip it on
    # in config.json on hosts that wire Curatables into Prometheus.
    prometheus_enabled: bool = False
    # Language of the kid screens before a kid is picked (profile picker);
    # each profile has its own.
    kid_language: str = "en"


@dataclass
class StorageConfig:
    path: str = ""
    default_mode: str = "cache"   # "library" = new videos are kept forever
    cache_days: int = 30
    default_resolution: str = "720p"
    subtitle_langs: str = "en"    # comma-separated codes, "all", or "" for none
    impersonate: str = "chrome"     # browser to impersonate: "chrome", "firefox", etc. or "" for none
    cookies_from_browser: str = ""  # browser name: "chrome", "firefox", etc.
    cookies_file: str = ""          # path to Netscape-format cookies.txt file
    min_free_disk_bytes: int = 2_147_483_648  # refuse writes when free space would drop below this (2 GB default)
    max_upload_bytes: int = 10_737_418_240    # absolute upper bound per upload (10 GB default)
    max_kid_upload_bytes: int = 524_288_000   # per-upload ceiling for kid uploads (500 MB default)
    max_download_bytes: int = 4_294_967_296   # yt-dlp refuses source files larger than this (4 GB default)
    max_concurrent_downloads: int = 1         # download + transcode jobs run at once (1 suits a Raspberry Pi)
    # Maintenance sweep (app/lifecycle.py) every N minutes: cache eviction
    # and event pruning. 0 disables it; cache_days<=0 keeps every video.
    cache_cleanup_interval_minutes: int = 60
    # Usage events (plays, views, searches) older than this are deleted
    # by the same background sweep. 0 keeps them forever.
    events_retention_days: int = 365
    # Where videos, uploads, thumbnails and channel pictures live. Empty
    # means inside `path`. Set by setup-external-drive.sh --media-only so
    # the media can sit on an exFAT/NTFS drive while the database stays
    # on the SD card.
    media_path: str = ""
    # Folders the import page may list video files from.
    import_roots: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.path:
            self.path = str(Path.home() / "curatables-data")


@dataclass
class ParentConfig:
    password_hash: str | None = None
    session_secret: str | None = None
    session_timeout_hours: int = 24
    session_epoch: int = 0
    setup_window_minutes: int = 30


DEFAULT_WISHLIST_BLOCKED_WORDS = [
    "horror", "scary", "creepy", "gore", "blood", "kill", "murder", "dead",
    "gun", "guns", "weapon", "fight", "violent", "prank", "sexy", "nsfw",
    "porn", "xxx", "drugs", "drunk", "beer", "18+",
]


@dataclass
class WishlistConfig:
    """Rules for the YouTube results a kid sees on the wishlist page."""
    blocked_words: list[str] = field(
        default_factory=lambda: list(DEFAULT_WISHLIST_BLOCKED_WORDS))
    # Non-empty: only results from these channel names are shown.
    allowed_channels: list[str] = field(default_factory=list)
    max_results: int = 12
    max_duration_minutes: int = 30


@dataclass
class ImportConfig:
    """The import page's online lookups for pictures and descriptions."""
    lookups_enabled: bool = True
    # Optional: a TMDB API key (v3) or read access token (v4) adds
    # posters and overviews from The Movie Database.
    tmdb_api_key: str = ""


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    parent: ParentConfig = field(default_factory=ParentConfig)
    wishlist: WishlistConfig = field(default_factory=WishlistConfig)
    imports: ImportConfig = field(default_factory=ImportConfig)

    @property
    def data_dir(self) -> Path:
        return Path(self.storage.path)

    @property
    def media_dir(self) -> Path:
        return Path(self.storage.media_path) if self.storage.media_path else self.data_dir

    @property
    def is_first_run(self) -> bool:
        return self.parent.password_hash is None

    def config_path(self) -> Path:
        return self.data_dir / "config.json"

    def save(self) -> None:
        """Save config atomically — write to temp file then rename.
        This prevents corruption if power is lost mid-write."""
        path = self.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        # Holds the password hash and session secret: owner-only.
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(self), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)  # atomic on POSIX


def load_config(data_dir: str | None = None) -> Config:
    """Load config from disk, or return defaults."""
    config = Config()
    if data_dir:
        config.storage.path = data_dir

    config_path = config.config_path()
    if config_path.exists():
        with open(config_path) as f:
            data = json.load(f)
        moved_to = _relocation_target(data, config_path)
        if moved_to is not None and not data_dir:
            if not moved_to.exists():
                # Starting with only the pointer would mean no password
                # and first-run setup open to the network.
                raise RuntimeError(
                    f"The library was moved to {moved_to.parent}, but it is not "
                    "there (is the drive mounted?). Refusing to start.")
            with open(moved_to) as f:
                data = json.load(f)
        config.server = ServerConfig(**data.get("server", {}))
        config.storage = StorageConfig(**data.get("storage", {}))
        config.parent = ParentConfig(**data.get("parent", {}))
        config.wishlist = WishlistConfig(**data.get("wishlist", {}))
        config.imports = ImportConfig(**data.get("imports", {}))
        if data_dir:
            config.storage.path = data_dir

    return config


def _relocation_target(data: dict, config_path: Path) -> Path | None:
    """After the library is moved from the dashboard, the old location
    keeps a pointer config.json holding only the new storage.path.
    Return the real config.json it points to, if there is one."""
    if set(data) != {"storage"}:
        return None
    target = Path(data["storage"].get("path", "")) / "config.json"
    if target == config_path:
        return None
    return target


MEDIA_SUBDIRS = ("videos", "thumbnails", "uploads", "uploads/.tmp")


def ensure_directories(config: Config) -> None:
    """Create all required data directories.

    A separate media folder is never created here: if it is missing the
    drive is not mounted, and starting would scatter new files over the
    SD card.
    """
    base = config.data_dir
    for subdir in ("db", "logs"):
        (base / subdir).mkdir(parents=True, exist_ok=True)
    media = config.media_dir
    if config.storage.media_path and not media.is_dir():
        raise RuntimeError(
            f"The media folder {media} is missing (is the drive plugged in and "
            "mounted?). Refusing to start.")
    for subdir in MEDIA_SUBDIRS:
        (media / subdir).mkdir(parents=True, exist_ok=True)
