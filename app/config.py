"""Configuration management for curatables server."""

import json
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


@dataclass
class StorageConfig:
    path: str = ""
    default_mode: str = "cache"
    cache_days: int = 30
    default_resolution: str = "720p"
    subtitle_langs: str = "en"    # comma-separated codes, "all", or "" for none
    impersonate: str = "chrome"     # browser to impersonate: "chrome", "firefox", etc. or "" for none
    cookies_from_browser: str = ""  # browser name: "chrome", "firefox", etc.
    cookies_file: str = ""          # path to Netscape-format cookies.txt file
    min_free_disk_bytes: int = 2_147_483_648  # refuse writes when free space would drop below this (2 GB default)
    max_upload_bytes: int = 10_737_418_240    # absolute upper bound per upload (10 GB default)
    max_kid_upload_bytes: int = 524_288_000   # per-upload ceiling for kid uploads (500 MB default)
    # Cache auto-cleanup: a background task in app/main.py lifespan runs
    # StorageService.evict_expired every N minutes. Set to 0 to disable
    # the sweep entirely; cache_days<=0 also short-circuits (keep forever).
    cache_cleanup_interval_minutes: int = 60

    def __post_init__(self):
        if not self.path:
            self.path = str(Path.home() / "curatables-data")


@dataclass
class ParentConfig:
    password_hash: str | None = None
    session_secret: str | None = None
    session_timeout_hours: int = 24


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    parent: ParentConfig = field(default_factory=ParentConfig)

    @property
    def data_dir(self) -> Path:
        return Path(self.storage.path)

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
        with open(tmp_path, "w") as f:
            json.dump(asdict(self), f, indent=2)
            f.flush()
            import os
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
        config.server = ServerConfig(**data.get("server", {}))
        config.storage = StorageConfig(**data.get("storage", {}))
        config.parent = ParentConfig(**data.get("parent", {}))
        if data_dir:
            config.storage.path = data_dir

    return config


def ensure_directories(config: Config) -> None:
    """Create all required data directories."""
    base = config.data_dir
    for subdir in ["db", "videos", "thumbnails/custom", "logs",
                   "uploads", "uploads/.tmp"]:
        (base / subdir).mkdir(parents=True, exist_ok=True)
