#!/usr/bin/env python3
"""Create a demo library for screenshots and manual testing.

Usage:
    python scripts/seed_demo.py /tmp/curatables-demo
    python run.py --data-dir /tmp/curatables-demo --port 8090

Parent password: demo-password. Profiles: Ana (simple: watch + react),
Ben (everything on, PIN 1234). Videos are 6-second ffmpeg test clips
with generated thumbnails, so nothing is downloaded.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import ensure_directories, load_config  # noqa: E402
from app.db.connection import create_connection, get_db_path  # noqa: E402
from app.db.schema import init_schema  # noqa: E402
from app.models import Video  # noqa: E402
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository  # noqa: E402
from app.services.auth import AuthService  # noqa: E402
from app.services.profiles import ProfileService  # noqa: E402
from app.services.thumbnails import ThumbnailService  # noqa: E402

CHANNELS = {
    "Animals": ["Ducks at the pond", "Sleepy cats", "Elephant bath time"],
    "Trains": ["Steam train in the hills", "Building a toy railway"],
    "Music": ["Counting song", "Drum circle", "Rainy day lullaby"],
}
COLORS = ["0x2a9d8f", "0xe9c46a", "0xf4a261", "0xe76f51", "0x264653",
          "0x8ab17d", "0x6d597a", "0x457b9d"]


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                   check=True)


def main(data_dir: str) -> None:
    config = load_config(data_dir)
    ensure_directories(config)
    if not config.parent.password_hash:
        AuthService(config).set_password("demo-password")
    conn = create_connection(get_db_path(config.data_dir))
    init_schema(conn)

    channels = ChannelRepository(conn)
    videos = VideoRepository(conn)
    thumbs = ThumbnailService(config.data_dir)
    profiles = ProfileService(ProfileRepository(conn))

    n = 0
    for channel_name, titles in CHANNELS.items():
        channel_id = channels.create(channel_name)
        for title in titles:
            video_id = f"demo_{n:03d}"
            color = COLORS[n % len(COLORS)]
            vdir = config.data_dir / "videos" / video_id
            vdir.mkdir(parents=True, exist_ok=True)
            _ffmpeg("-f", "lavfi", "-i", "testsrc=duration=6:size=640x360:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", "-movflags", "+faststart", str(vdir / "video.mp4"))
            thumb = thumbs.get_path(video_id)
            thumb.parent.mkdir(parents=True, exist_ok=True)
            _ffmpeg("-f", "lavfi", "-i", f"color=c={color}:size=640x360",
                    "-frames:v", "1", str(thumb))
            if videos.get(video_id) is None:
                videos.insert(Video(
                    video_id=video_id, title=title, original_title=title,
                    channel_id=channel_id, channel_name=channel_name,
                    status="active", download_status="ready",
                    storage_mode="cache", extractor="demo",
                ))
                videos.update(video_id, cached_at="2026-01-01 00:00:00",
                              file_size=(vdir / "video.mp4").stat().st_size)
            n += 1

    if not profiles.list():
        profiles.create(name="ana", display_name="Ana", avatar="bear",
                        allow_comments=False, allow_tags=False,
                        allow_customize=False, allow_upload=False,
                        auto_subtitles=False)
        profiles.create(name="ben", display_name="Ben", avatar="rocket",
                        theme="playful", pin="1234", search_mode="curated")
    conn.close()
    print(f"Demo library ready in {config.data_dir} (parent password: demo-password)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
